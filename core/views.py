import difflib
import os
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth import login
from django.contrib import messages
from django.db import transaction, IntegrityError
from django.db.models import Q, Prefetch
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.views.decorators.http import require_POST
from ddgs import DDGS
from openai import OpenAI

# Importation de l'intégralité des modèles requis
from .models import (
    Support, ReferencePartagee, SalonDeDiscussion,
    InvitationSalon, MessageSalon, Profile, Filiere, CoursProgramme
)
from .forms import InscriptionForm

# --- CONFIGURATION INITIALE ---
AI_API_KEY = os.environ.get("OPENAI_API_KEY")
ai_client = OpenAI(api_key=AI_API_KEY, timeout=4.0) if AI_API_KEY else None


def home_publique(request):
    """Page d'accueil publique affichant les filières, niveaux et supports disponibles."""
    context = {
        'niveaux': ['L1', 'L2', 'L3', 'M1', 'M2'],
        'filieres': Filiere.objects.only('id', 'nom'),
        'tous_les_supports': Support.objects.select_related('cours__filiere').only(
            'id', 'titre', 'cours__intitule', 'cours__filiere__nom', 'cours__promotion'
        )[:12]
    }
    return render(request, 'core/home_publique.html', context)


@login_required
def dashboard(request):
    """Tableau de bord étudiant : flux collaboratif, recherche hybride intranet/web
    et génération de fiches d'exercices corrigés par IA.
    """
    profile, _ = Profile.objects.select_related('filiere').get_or_create(
        user_id=request.user.id,
        defaults={
            'user': request.user,
            'filiere': Filiere.objects.first(),
            'promotion': 'L1'
        }
    )

    query = request.GET.get('q', '').strip()

    # 1. Traitement du partage de ressource (POST)
    if request.method == 'POST':
        titre = request.POST.get('titre', '').strip()
        contenu = request.POST.get('description_ou_lien', '').strip()
        if titre and contenu:
            ReferencePartagee.objects.create(
                etudiant=request.user,
                titre=titre,
                description_ou_lien=contenu,
                filiere=profile.filiere,
                promotion=profile.promotion
            )
            messages.success(request, "Ressource d'entraide partagée avec succès !")
            return redirect('dashboard')
        messages.error(request, "Veuillez remplir tous les champs obligatoires.")

    # 2. Préparation des flux de données de base
    supports_initiaux = Support.objects.filter(
        cours__filiere=profile.filiere,
        cours__promotion=profile.promotion
    ).select_related('cours')

    supports_locaux = supports_initiaux

    references_filiere = ReferencePartagee.objects.filter(
        filiere=profile.filiere,
        promotion=profile.promotion
    ).select_related('etudiant').only(
        'id', 'titre', 'description_ou_lien', 'date_publication', 'etudiant__username'
    ).order_by('-date_publication')[:20]

    supports_internet = []
    explication_internet = ""
    suggestions_similaires = []
    recherche_active = bool(query)

    # 3. Moteur de Recherche Multi-Niveaux + Orchestration IA
    if query:
        resultats_locaux = supports_initiaux.filter(
            Q(titre__icontains=query) |
            Q(exemples__icontains=query) |
            Q(exercices__icontains=query) |
            Q(cours__intitule__icontains=query)
        ).distinct()

        if ai_client and profile.filiere:
            try:
                prompt_ia = f"""
                Tu es l'assistant IA de l'E-Library pour l'université AUK.
                L'étudiant recherche des informations sur le thème : "{query}" (Niveau: {profile.promotion} - Filière: {profile.filiere.nom}).

                Génère un complément pédagogique structuré EXCLUSIVEMENT en HTML épuré (pas de balises globales html/body, pas de blocs markdown ```html).

                Inclus obligatoirement :
                1. <h3 class="text-base font-bold text-indigo-950 mb-2">💡 Exemples Concrets d'Application</h3>
                2. <h3 class="text-base font-bold text-indigo-950 mt-4 mb-2">📝 Exercice d'Entraînement</h3>
                3. <h3 class="text-base font-bold text-emerald-800 mt-2 mb-2">🛠️ Résolution Détaillée</h3>
                """
                response = ai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system",
                         "content": "Tu es un professeur d'université expert. Tu n'utilises que du HTML sémantique bien aéré."},
                        {"role": "user", "content": prompt_ia}
                    ],
                    max_tokens=1000,
                    temperature=0.3
                )
                explication_internet = response.choices[0].message.content
            except Exception:
                explication_internet = "<p class='text-amber-600 font-medium'>⚠️ L'assistant IA est temporairement indisponible. Bascule sur les moteurs de secours.</p>"

        if not resultats_locaux.exists():
            filiere_nom = profile.filiere.nom if profile.filiere else ""
            if not explication_internet:
                try:
                    with DDGS(timeout=2) as ddgs_client:
                        recherche_texte = list(ddgs_client.text(f"{query} cours {filiere_nom}", max_results=1))
                        if recherche_texte and len(recherche_texte[0].get('body', '')) > 20:
                            explication_internet = f"<p class='font-semibold text-gray-800 mb-2'>Extrait du Web :</p>{recherche_texte[0]['body']}"
                except Exception:
                    pass

            try:
                with DDGS(timeout=2) as ddgs_client:
                    recherche_pdf = list(ddgs_client.text(f"{query} filetype:pdf university lecture", max_results=3))
                    supports_internet = [
                        {
                            'titre': res.get('title'),
                            'lien': res.get('href'),
                            'description': res.get('body')
                        } for res in recherche_pdf if res.get('href')
                    ]
            except Exception:
                pass

            if not explication_internet and not supports_internet:
                titres_locaux = list(supports_initiaux.values_list('titre', flat=True)[:100])
                correspondances = difflib.get_close_matches(query, titres_locaux, n=3, cutoff=0.25)
                if correspondances:
                    suggestions_similaires = supports_initiaux.filter(titre__in=correspondances).only('id', 'titre')

        supports_locaux = resultats_locaux

    context = {
        'profile': profile,
        'supports_locaux': supports_locaux,
        'references_filiere': references_filiere,
        'supports_internet': supports_internet,
        'explication_internet': explication_internet,
        'suggestions_similaires': suggestions_similaires,
        'recherche_active': recherche_active,
        'query': query
    }
    return render(request, 'core/dashboard.html', context)


@login_required
def gestion_salons(request):
    """Vue pour afficher, rejoindre et administrer les espaces de discussion privés."""
    profile = get_object_or_404(Profile.objects.select_related('filiere'), user_id=request.user.id)

    if request.method == 'POST' and 'creer_salon' in request.POST:
        nom_salon = request.POST.get('nom_salon', '').strip()
        if nom_salon:
            with transaction.atomic():
                nouveau_salon = SalonDeDiscussion.objects.create(nom=nom_salon, createur=request.user)
                nouveau_salon.membres.add(request.user)
            messages.success(request, f"Le salon '{nom_salon}' a été créé avec succès.")
            return redirect('gestion_salons')
        messages.error(request, "Le nom du salon ne peut pas être vide.")

    membres_queryset = User.objects.only('id', 'username')
    mes_salons = SalonDeDiscussion.objects.filter(
        Q(createur=request.user) | Q(membres=request.user)
    ).select_related('createur').prefetch_related(
        Prefetch('membres', queryset=membres_queryset)
    ).distinct()

    invitations_recues = InvitationSalon.objects.filter(
        invite=request.user,
        accepte__isnull=True
    ).select_related('hote', 'salon')

    camarades = list(User.objects.filter(
        profile__filiere=profile.filiere,
        is_active=True
    ).select_related('profile').exclude(id=request.user.id).only(
        'id', 'username', 'first_name', 'last_name', 'profile__filiere_id'
    ))

    invitations_en_cours = InvitationSalon.objects.filter(
        salon__createur=request.user,
        accepte__isnull=True
    ).values_list('salon_id', 'invite_id')

    invitations_map = {}
    for salon_id, invite_id in invitations_en_cours:
        invitations_map.setdefault(salon_id, set()).add(invite_id)

    for salon in mes_salons:
        if salon.createur_id == request.user.id:
            membres_ids = {m.id for m in salon.membres.all()}
            invites_attente = invitations_map.get(salon.id, set())
            exclus_ids = membres_ids.union(invites_attente)
            salon.camarades_invitables = [c for c in camarades if c.id not in exclus_ids]

    context = {
        'mes_salons': mes_salons,
        'invitations_recues': invitations_recues,
    }
    return render(request, 'core/salons.html', context)


@login_required
@require_POST
def envoyer_invitation(request, salon_id):
    """Permet au créateur d'un salon d'inviter un camarade éligible de sa même filière."""
    salon = get_object_or_404(SalonDeDiscussion, id=salon_id, createur=request.user)
    invite_id = request.POST.get('invite_id')

    if not invite_id:
        return HttpResponseBadRequest("L'identifiant de l'invité est manquant.")

    invite = get_object_or_404(User.objects.select_related('profile'), id=invite_id, is_active=True)
    hote_profile = get_object_or_404(Profile, user_id=request.user.id)

    if invite.profile.filiere_id != hote_profile.filiere_id:
        messages.error(request, "Vous ne pouvez pas inviter un étudiant d'une autre filière.")
        return redirect('gestion_salons')

    if salon.membres.filter(id=invite.id).exists():
        messages.error(request, f"{invite.username} fait déjà partie de ce salon.")
        return redirect('gestion_salons')

    invitation, cree = InvitationSalon.objects.get_or_create(
        salon=salon,
        hote=request.user,
        invite=invite,
        accepte=None
    )

    if cree:
        messages.success(request, f"Invitation envoyée avec succès à {invite.username}.")
    else:
        messages.info(request, f"Une invitation est déjà en attente pour {invite.username}.")

    return redirect('gestion_salons')


@login_required
@require_POST
def repondre_invitation(request, invitation_id, action):
    """Gère l'acceptation ou le refus sécurisé avec verrouillage pessimiste."""
    if action not in ['accepter', 'refuser']:
        raise Http404("Action non valide.")

    with transaction.atomic():
        try:
            invitation = (
                InvitationSalon.objects.select_for_update()
                    .select_related('salon')
                    .get(id=invitation_id, invite=request.user, accepte__isnull=True)
            )
        except InvitationSalon.DoesNotExist:
            raise Http404("Cette invitation a déjà été traitée ou n'existe pas.")

        if action == 'accepter':
            invitation.accepte = True
            invitation.salon.membres.add(request.user)
            invitation.save()

            InvitationSalon.objects.filter(
                salon=invitation.salon,
                invite=request.user,
                accepte__isnull=True
            ).exclude(id=invitation.id).update(accepte=False)

            messages.success(request, f"Vous avez rejoint le salon '{invitation.salon.nom}'.")
        else:
            invitation.accepte = False
            invitation.save()
            messages.info(request, f"Invitation pour le salon '{invitation.salon.nom}' déclinée.")

    return redirect('gestion_salons')


def inscription(request):
    """Inscription sécurisée avec transaction atomique unifiée (User + Profile)."""
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = InscriptionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save(commit=False)
                user.set_password(form.cleaned_data['password'])
                user.save()

                Profile.objects.create(
                    user=user,
                    filiere=form.cleaned_data['filiere'],
                    promotion=form.cleaned_data['promotion']
                )

            login(request, user)
            messages.success(request, "Inscription réussie ! Bienvenue sur votre espace d'étude.")
            return redirect('dashboard')
        except IntegrityError:
            form.add_error('username', "Ce nom d'utilisateur est déjà utilisé.")
        except Exception:
            messages.error(request, "Une erreur système est survenue. Veuillez réessayer.")

    return render(request, 'core/inscription.html', {'form': form})


from django.shortcuts import render
from .models import Filiere, CoursProgramme


def voir_programme(request):
    filiere_id = request.GET.get('filiere')

    # 1. Récupération de la filière et des cours
    filiere_nom = ""
    liste_par_promotion = {}

    if filiere_id:
        filiere_obj = Filiere.objects.get(id=filiere_id)
        filiere_nom = filiere_obj.nom

        # Organisation par promotion (L1 à M2)
        for promo in ['L1', 'L2', 'L3', 'M1', 'M2']:
            cours = CoursProgramme.objects.filter(filiere=filiere_obj, promotion=promo)
            if cours.exists():
                liste_par_promotion[promo] = cours

    return render(request, 'core/programme.html', {
        'filieres': Filiere.objects.all(),
        'filiere_nom': filiere_nom,
        'liste_par_promotion': liste_par_promotion,
        'filiere_selectionnee': filiere_id,
    })


@login_required
def voir_salon(request, salon_id):
    """Affiche la page d'un salon de discussion si l'étudiant est membre."""
    salon = get_object_or_404(
        SalonDeDiscussion.objects.prefetch_related('membres'),
        id=salon_id
    )

    if request.user not in salon.membres.all():
        messages.error(request, "Vous n'avez pas accès à ce salon d'études.")
        return redirect('gestion_salons')

    return render(request, 'core/salon_detail.html', {'salon': salon})


@login_required
def api_messages(request, salon_id):
    """API pour récupérer et envoyer des messages en arrière-plan."""
    salon = get_object_or_404(SalonDeDiscussion.objects.prefetch_related('membres'), id=salon_id)

    if request.user not in salon.membres.all():
        return JsonResponse({'error': 'Non autorisé'}, status=403)

    if request.method == 'POST':
        texte = request.POST.get('texte', '').strip()
        if texte:
            msg = MessageSalon.objects.create(salon=salon, auteur=request.user, texte=texte)
            return JsonResponse({
                'id': msg.id,
                'auteur': msg.auteur.username,
                'texte': msg.texte,
                'date': msg.date_envoi.strftime('%H:%M')
            })

    messages_qs = salon.messages_contenus.select_related('auteur').only(
        'id', 'texte', 'date_envoi', 'auteur__username'
    ).order_by('-date_envoi')[:50]

    messages_list = [{
        'id': m.id,
        'auteur': m.auteur.username,
        'texte': m.texte,
        'date': m.date_envoi.strftime('%H:%M'),
        'est_me': (m.auteur_id == request.user.id)
    } for m in reversed(messages_qs)]

    return JsonResponse({'messages': messages_list})


from django.contrib.admin.views.decorators import staff_member_required


@staff_member_required  # Sécurité : réservé aux admins
@staff_member_required
def initialiser_les_cours(request):
    """
    Injection intégrale et optimisée du catalogue complet des cours.
    """
    DATA_PROGRAMMES = {
        "Polytechnique": [
            {"code_ue": "POLY111", "intitule": "Algèbre Linéaire et Géométrie Analytique", "promotion": "L1"},
            {"code_ue": "POLY112", "intitule": "Analyse Mathématique I : Suites et Fonctions", "promotion": "L1"},
            {"code_ue": "POLY113", "intitule": "Physique Générale : Mécanique du Point et Optique", "promotion": "L1"},
            {"code_ue": "POLY114", "intitule": "Chimie Générale, Atomistique et Liaisons", "promotion": "L1"},
            {"code_ue": "POLY211", "intitule": "Analyse Mathématique II : Intégrales et Équations Différentielles",
             "promotion": "L2"},
            {"code_ue": "POLY212", "intitule": "Mécanique Rationnelle et Cinématique", "promotion": "L2"},
            {"code_ue": "POLY213", "intitule": "Électromagnétisme et Électricité Générale", "promotion": "L2"},
            {"code_ue": "POLY214", "intitule": "Algorithmique et Programmation Numérique en Python", "promotion": "L2"},
            {"code_ue": "POLY311", "intitule": "Résistance des Matériaux (RDM) Fondamentale", "promotion": "L3"},
            {"code_ue": "POLY312", "intitule": "Mécanique des Fluides Newtoniens", "promotion": "L3"},
            {"code_ue": "POLY313", "intitule": "Thermodynamique Appliquée et Cycles Thermiques", "promotion": "L3"},
            {"code_ue": "POLY314", "intitule": "Probabilités, Statistiques et Analyse de Données", "promotion": "L3"},
            {"code_ue": "POLY411", "intitule": "Calcul des Structures et Méthode des Éléments Finis",
             "promotion": "M1"},
            {"code_ue": "POLY412", "intitule": "Automatique Linéaire, Asservissement et Régulation", "promotion": "M1"},
            {"code_ue": "POLY413", "intitule": "Recherche Opérationnelle, Graphes et Optimisation", "promotion": "M1"},
            {"code_ue": "POLY414", "intitule": "Sciences des Matériaux : Cristallographie et Métallurgie",
             "promotion": "M1"},
            {"code_ue": "POLY511", "intitule": "Management de Projet Industriel et Supply Chain", "promotion": "M2"},
            {"code_ue": "POLY512", "intitule": "Énergies Renouvelables et Transition Énergétique", "promotion": "M2"},
            {"code_ue": "POLY513", "intitule": "Fiabilité des Systèmes et Maintenance Industrielle", "promotion": "M2"},
            {"code_ue": "POLY514", "intitule": "Projet de Fin d'Études (PFE) et Stage Ingénieur", "promotion": "M2"},
        ],
        "Génie Civil": [
            {"code_ue": "CIVI111", "intitule": "Mathématiques pour l'Ingénieur et Outils Numériques",
             "promotion": "L1"},
            {"code_ue": "CIVI112", "intitule": "Mécanique Générale et Statique des Solides", "promotion": "L1"},
            {"code_ue": "CIVI113", "intitule": "Géologie de l'Ingénieur et Minéralogie", "promotion": "L1"},
            {"code_ue": "CIVI114", "intitule": "Introduction au Dessin Technique et Lecture de Plans",
             "promotion": "L1"},
            {"code_ue": "CIVI211", "intitule": "Résistance des Matériaux I : Poutres et Sollicitations simples",
             "promotion": "L2"},
            {"code_ue": "CIVI212", "intitule": "Physique des Matériaux de Construction (Béton, Acier, Bois)",
             "promotion": "L2"},
            {"code_ue": "CIVI213", "intitule": "Topographie, Levés de Terrains et Implantation", "promotion": "L2"},
            {"code_ue": "CIVI214", "intitule": "Hydraulique Générale et Écoulements en Canaux", "promotion": "L2"},
            {"code_ue": "CIVI311", "intitule": "Résistance des Matériaux II : Systèmes Hyperstatiques",
             "promotion": "L3"},
            {"code_ue": "CIVI312", "intitule": "Mécanique des Sols I : Caractéristiques Physico-chimiques",
             "promotion": "L3"},
            {"code_ue": "CIVI313", "intitule": "Béton Armé I : Principes Fondamentaux et Calcul aux ELU/ELS",
             "promotion": "L3"},
            {"code_ue": "CIVI314", "intitule": "CAO Appliquée au Bâtiment (AutoCAD / Robot Structural Analysis)",
             "promotion": "L3"},
            {"code_ue": "CIVI411", "intitule": "Mécanique des Sols II : Fondations Superficielles et Profondes",
             "promotion": "M1"},
            {"code_ue": "CIVI412", "intitule": "Béton Armé II : Eurocode 2 et Calcul des Structures Complexes",
             "promotion": "M1"},
            {"code_ue": "CIVI413", "intitule": "Charpente Métallique et Mixte (Eurocode 3)", "promotion": "M1"},
            {"code_ue": "CIVI414", "intitule": "Infrastructures Routières, Terrassement et Assainissement",
             "promotion": "M1"},
            {"code_ue": "CIVI511", "intitule": "Ouvrages d'Art : Ponts, Tunnels et Soutènements", "promotion": "M2"},
            {"code_ue": "CIVI512", "intitule": "Dynamique des Structures et Génie Parasismique", "promotion": "M2"},
            {"code_ue": "CIVI513", "intitule": "Planification, Plan de Charge et Management BIM", "promotion": "M2"},
            {"code_ue": "CIVI514", "intitule": "Projet de Fin d'Études (PFE) et Synthèse Technique", "promotion": "M2"},
        ],
        "Architecture": [
            {"code_ue": "ARCH111", "intitule": "Introduction à l'Architecture et Théorie des Formes",
             "promotion": "L1"},
            {"code_ue": "ARCH112", "intitule": "Dessin Technique, Géométrie Descriptive et Perspective",
             "promotion": "L1"},
            {"code_ue": "ARCH113", "intitule": "Histoire Générale de l'Art et de l'Espace Bâti", "promotion": "L1"},
            {"code_ue": "ARCH114", "intitule": "Physique du Bâtiment : Lumière et Acoustique Initiale",
             "promotion": "L1"},
            {"code_ue": "ARCH211", "intitule": "Atelier de Projet Architectural I : La Cellule et l'Habitat",
             "promotion": "L2"},
            {"code_ue": "ARCH212", "intitule": "RDM (Résistance des Matériaux) appliquée aux Structures",
             "promotion": "L2"},
            {"code_ue": "ARCH213", "intitule": "Matériaux de Construction traditionnels et modernes",
             "promotion": "L2"},
            {"code_ue": "ARCH214", "intitule": "Topographie et Relevé de l'Édifice", "promotion": "L2"},
            {"code_ue": "ARCH311", "intitule": "Atelier de Projet Architectural II : Équipements Publics Épurés",
             "promotion": "L3"},
            {"code_ue": "ARCH312", "intitule": "Technologie du Bâtiment et Systèmes Constructifs", "promotion": "L3"},
            {"code_ue": "ARCH313", "intitule": "Urbanisme et Morphologie Urbaine", "promotion": "L3"},
            {"code_ue": "ARCH314", "intitule": "CAO/DAO : Modélisation 2D/3D (AutoCAD / Revit)", "promotion": "L3"},
            {"code_ue": "ARCH411", "intitule": "Conception Architecturale Complexe et Structures Spéciales",
             "promotion": "M1"},
            {"code_ue": "ARCH412", "intitule": "Architecture Bioclimatique et Haute Qualité Environnementale (HQE)",
             "promotion": "M1"},
            {"code_ue": "ARCH413", "intitule": "Restauration, Réhabilitation et Patrimoine Bâti", "promotion": "M1"},
            {"code_ue": "ARCH414", "intitule": "Sociologie Urbaine et Politiques de l'Habitat", "promotion": "M1"},
            {"code_ue": "ARCH511", "intitule": "Projet de Fin d'Études (PFE) : Grand Équipement", "promotion": "M2"},
            {"code_ue": "ARCH512", "intitule": "Pratique Professionnelle, Déontologie et Droit de la Construction",
             "promotion": "M2"},
            {"code_ue": "ARCH513", "intitule": "Économie de la Construction, Métré et Cahier des Charges",
             "promotion": "M2"},
            {"code_ue": "ARCH514", "intitule": "Management de Projet et BIM", "promotion": "M2"},
        ],
        "Pétrole et Gaz": [
            {"code_ue": "PETR111", "intitule": "Algèbre et Analyse pour l'Ingénieur", "promotion": "L1"},
            {"code_ue": "PETR112", "intitule": "Chimie Organique et Solutions Aqueuses", "promotion": "L1"},
            {"code_ue": "PETR113", "intitule": "Géologie Générale et Cristallographie", "promotion": "L1"},
            {"code_ue": "PETR114", "intitule": "Introduction à l'Industrie Pétrolière (Amont/Aval)", "promotion": "L1"},
            {"code_ue": "PETR211", "intitule": "Thermodynamique Chimique et Transfert de Chaleur", "promotion": "L2"},
            {"code_ue": "PETR212", "intitule": "Mécanique des Fluides Incompressibles", "promotion": "L2"},
            {"code_ue": "PETR213", "intitule": "Géologie Structurale et Sédimentologie", "promotion": "L2"},
            {"code_ue": "PETR214", "intitule": "Informatique Numérique et Programmation (Python)", "promotion": "L2"},
            {"code_ue": "PETR311", "intitule": "Ingénierie des Réservoirs I : Propriétés des Roches",
             "promotion": "L3"},
            {"code_ue": "PETR312", "intitule": "Techniques de Forage et Complétion des Puits", "promotion": "L3"},
            {"code_ue": "PETR313", "intitule": "Évaluation des Formations (Diagraphies)", "promotion": "L3"},
            {"code_ue": "PETR314", "intitule": "Géophysique d'Exploration et Sismique Réflexion", "promotion": "L3"},
            {"code_ue": "PETR411", "intitule": "Ingénierie des Réservoirs II : Simulation Numérique",
             "promotion": "M1"},
            {"code_ue": "PETR412", "intitule": "Production du Pétrole et du Gaz : Activation et Collecte",
             "promotion": "M1"},
            {"code_ue": "PETR413", "intitule": "Raffinage du Pétrole et Pétrochimie", "promotion": "M1"},
            {"code_ue": "PETR414", "intitule": "Sécurité Industrielle, HSE et Risques Technologiques",
             "promotion": "M1"},
            {"code_ue": "PETR511", "intitule": "Économie Pétrolière, Droit Minier et Contrats Partagés",
             "promotion": "M2"},
            {"code_ue": "PETR512", "intitule": "Traitement du Gaz Naturel et GNL", "promotion": "M2"},
            {"code_ue": "PETR513", "intitule": "Transport, Stockage et Logistique des Hydrocarbures",
             "promotion": "M2"},
            {"code_ue": "PETR514", "intitule": "Gestion des Champs Matures et Abandon des Puits", "promotion": "M2"},
        ],
        "Informatique": [
            {"code_ue": "INFO111", "intitule": "Algorithmique Fondamentale et Structures de Données",
             "promotion": "L1"},
            {"code_ue": "INFO112", "intitule": "Introduction à l'Architecture des Ordinateurs et SE",
             "promotion": "L1"},
            {"code_ue": "INFO113", "intitule": "Logique Mathématique et Algèbre de Boole", "promotion": "L1"},
            {"code_ue": "INFO114", "intitule": "Programmation Impérative (Langage C / Python)", "promotion": "L1"},
            {"code_ue": "INFO211", "intitule": "Programmation Orientée Objet (Java / C++)", "promotion": "L2"},
            {"code_ue": "INFO212", "intitule": "Systèmes de Gestion de Bases de Données (SQL)", "promotion": "L2"},
            {"code_ue": "INFO213", "intitule": "Réseaux Informatiques : Modèles OSI et TCP/IP", "promotion": "L2"},
            {"code_ue": "INFO214", "intitule": "Développement Web Front-End (HTML, CSS, JS)", "promotion": "L2"},
            {"code_ue": "INFO311", "intitule": "Génie Logiciel : Conception UML et Design Patterns", "promotion": "L3"},
            {"code_ue": "INFO312", "intitule": "Développement Web Avancé (Framework Django)", "promotion": "L3"},
            {"code_ue": "INFO313", "intitule": "Administration Systèmes et Sécurité Réseaux (Linux)",
             "promotion": "L3"},
            {"code_ue": "INFO314", "intitule": "Recherche Opérationnelle et Optimisation Linéaire", "promotion": "L3"},
            {"code_ue": "INFO411", "intitule": "Intelligence Artificielle et Machine Learning", "promotion": "M1"},
            {"code_ue": "INFO412", "intitule": "Systèmes Distribués, Cloud Computing et Virtualisation",
             "promotion": "M1"},
            {"code_ue": "INFO413", "intitule": "Bases de Données NoSQL et Écosystème Big Data", "promotion": "M1"},
            {"code_ue": "INFO414", "intitule": "Cryptographie et Sécurité des Systèmes d'Information",
             "promotion": "M1"},
            {"code_ue": "INFO511", "intitule": "Architecture Microservices, Devops et CI/CD", "promotion": "M2"},
            {"code_ue": "INFO512", "intitule": "Internet des Objets (IoT) et Systèmes Embarqués", "promotion": "M2"},
            {"code_ue": "INFO513", "intitule": "Gestion de Projets Agiles (Scrum) et Audit", "promotion": "M2"},
            {"code_ue": "INFO514", "intitule": "Vision par Ordinateur et Traitement du Langage (NLP)",
             "promotion": "M2"},
        ],
        "Économie": [
            {"code_ue": "ECON111", "intitule": "Introduction à la Microéconomie", "promotion": "L1"},
            {"code_ue": "ECON112", "intitule": "Introduction à la Macroéconomie", "promotion": "L1"},
            {"code_ue": "ECON113", "intitule": "Histoire des Faits Économiques", "promotion": "L1"},
            {"code_ue": "ECON114", "intitule": "Mathématiques Appliquées à l'Économie", "promotion": "L1"},
            {"code_ue": "ECON211", "intitule": "Microéconomie Intermédiaire : Équilibre de Marché", "promotion": "L2"},
            {"code_ue": "ECON212", "intitule": "Macroéconomie Intermédiaire : Modèles IS-LM", "promotion": "L2"},
            {"code_ue": "ECON213", "intitule": "Statistique Descriptive et Calcul des Probabilités", "promotion": "L2"},
            {"code_ue": "ECON214", "intitule": "Comptabilité Générale et Analyse Financière", "promotion": "L2"},
            {"code_ue": "ECON311", "intitule": "Économétrie I : Régression Linéaire", "promotion": "L3"},
            {"code_ue": "ECON312", "intitule": "Économie Monétaire, Bancaire et Marchés Financiers", "promotion": "L3"},
            {"code_ue": "ECON313", "intitule": "Économie Internationale : Échanges", "promotion": "L3"},
            {"code_ue": "ECON314", "intitule": "Économie du Développement et Croissance", "promotion": "L3"},
            {"code_ue": "ECON411", "intitule": "Économétrie II : Séries Temporelles", "promotion": "M1"},
            {"code_ue": "ECON412", "intitule": "Économie Industrielle, Régulation et Théorie des Jeux",
             "promotion": "M1"},
            {"code_ue": "ECON413", "intitule": "Évaluation Économique des Projets d'Investissement", "promotion": "M1"},
            {"code_ue": "ECON414", "intitule": "Économie Publique et Analyse des Politiques Fiscales",
             "promotion": "M1"},
            {"code_ue": "ECON511", "intitule": "Macroéconomie Avancée et Modélisation", "promotion": "M2"},
            {"code_ue": "ECON512", "intitule": "Finance Internationale et Gestion des Risques", "promotion": "M2"},
            {"code_ue": "ECON513", "intitule": "Théorie des Incitations et Régulation Économique", "promotion": "M2"},
            {"code_ue": "ECON514", "intitule": "Mémoire de Recherche ou Stage de Fin d'Études", "promotion": "M2"},
        ]
    }

    compteur_total = 0
    with transaction.atomic():
        for nom_filiere, liste_cours in DATA_PROGRAMMES.items():
            filiere_obj, _ = Filiere.objects.get_or_create(nom=nom_filiere)

            # On récupère les codes déjà existants pour cette filière
            codes_existants = set(CoursProgramme.objects.filter(filiere=filiere_obj).values_list('code_ue', flat=True))

            nouveaux_cours = [
                CoursProgramme(
                    filiere=filiere_obj,
                    code_ue=c["code_ue"],
                    intitule=c["intitule"],
                    promotion=c["promotion"]
                )
                for c in liste_cours if c["code_ue"] not in codes_existants
            ]

            if nouveaux_cours:
                CoursProgramme.objects.bulk_create(nouveaux_cours)
                compteur_total += len(nouveaux_cours)

    messages.success(request, f"Initialisation terminée : {compteur_total} nouveaux cours ajoutés.")
    return redirect('voir_programme')


from django.http import JsonResponse
import google.generativeai as genai

# Configure Gemini avec ta clé API
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash')


def chat_ia_view(request):
    if request.method == 'POST':
        user_text = request.POST.get('texte')
        # Appel à ton API IA ici
        response = genai.GenerativeModel('gemini-pro').generate_content(user_text)
        return JsonResponse({'message': response.text})
    return render(request, 'chat_ia.html')


from django.shortcuts import render


def voir_catalogue_complet(request):
    DATA_PROGRAMMES = {
        "Polytechnique": [
            {"code_ue": "POLY111", "intitule": "Algèbre Linéaire et Géométrie Analytique", "promotion": "L1"},
            {"code_ue": "POLY112", "intitule": "Analyse Mathématique I : Suites et Fonctions", "promotion": "L1"},
            {"code_ue": "POLY113", "intitule": "Physique Générale : Mécanique du Point et Optique", "promotion": "L1"},
            {"code_ue": "POLY114", "intitule": "Chimie Générale, Atomistique et Liaisons", "promotion": "L1"},
            {"code_ue": "POLY211", "intitule": "Analyse Mathématique II : Intégrales et Équations Différentielles",
             "promotion": "L2"},
            {"code_ue": "POLY212", "intitule": "Mécanique Rationnelle et Cinématique", "promotion": "L2"},
            {"code_ue": "POLY213", "intitule": "Électromagnétisme et Électricité Générale", "promotion": "L2"},
            {"code_ue": "POLY214", "intitule": "Algorithmique et Programmation Numérique en Python", "promotion": "L2"},
            {"code_ue": "POLY311", "intitule": "Résistance des Matériaux (RDM) Fondamentale", "promotion": "L3"},
            {"code_ue": "POLY312", "intitule": "Mécanique des Fluides Newtoniens", "promotion": "L3"},
            {"code_ue": "POLY313", "intitule": "Thermodynamique Appliquée et Cycles Thermiques", "promotion": "L3"},
            {"code_ue": "POLY314", "intitule": "Probabilités, Statistiques et Analyse de Données", "promotion": "L3"},
            {"code_ue": "POLY411", "intitule": "Calcul des Structures et Méthode des Éléments Finis",
             "promotion": "M1"},
            {"code_ue": "POLY412", "intitule": "Automatique Linéaire, Asservissement et Régulation", "promotion": "M1"},
            {"code_ue": "POLY413", "intitule": "Recherche Opérationnelle, Graphes et Optimisation", "promotion": "M1"},
            {"code_ue": "POLY414", "intitule": "Sciences des Matériaux : Cristallographie et Métallurgie",
             "promotion": "M1"},
            {"code_ue": "POLY511", "intitule": "Management de Projet Industriel et Supply Chain", "promotion": "M2"},
            {"code_ue": "POLY512", "intitule": "Énergies Renouvelables et Transition Énergétique", "promotion": "M2"},
            {"code_ue": "POLY513", "intitule": "Fiabilité des Systèmes et Maintenance Industrielle", "promotion": "M2"},
            {"code_ue": "POLY514", "intitule": "Projet de Fin d'Études (PFE) et Stage Ingénieur", "promotion": "M2"},
        ],
        "Génie Civil": [
            {"code_ue": "CIVI111", "intitule": "Mathématiques pour l'Ingénieur et Outils Numériques",
             "promotion": "L1"},
            {"code_ue": "CIVI112", "intitule": "Mécanique Générale et Statique des Solides", "promotion": "L1"},
            {"code_ue": "CIVI113", "intitule": "Géologie de l'Ingénieur et Minéralogie", "promotion": "L1"},
            {"code_ue": "CIVI114", "intitule": "Introduction au Dessin Technique et Lecture de Plans",
             "promotion": "L1"},
            {"code_ue": "CIVI211", "intitule": "Résistance des Matériaux I : Poutres et Sollicitations simples",
             "promotion": "L2"},
            {"code_ue": "CIVI212", "intitule": "Physique des Matériaux de Construction (Béton, Acier, Bois)",
             "promotion": "L2"},
            {"code_ue": "CIVI213", "intitule": "Topographie, Levés de Terrains et Implantation", "promotion": "L2"},
            {"code_ue": "CIVI214", "intitule": "Hydraulique Générale et Écoulements en Canaux", "promotion": "L2"},
            {"code_ue": "CIVI311", "intitule": "Résistance des Matériaux II : Systèmes Hyperstatiques",
             "promotion": "L3"},
            {"code_ue": "CIVI312", "intitule": "Mécanique des Sols I : Caractéristiques Physico-chimiques",
             "promotion": "L3"},
            {"code_ue": "CIVI313", "intitule": "Béton Armé I : Principes Fondamentaux et Calcul aux ELU/ELS",
             "promotion": "L3"},
            {"code_ue": "CIVI314", "intitule": "CAO Appliquée au Bâtiment (AutoCAD / Robot Structural Analysis)",
             "promotion": "L3"},
            {"code_ue": "CIVI411", "intitule": "Mécanique des Sols II : Fondations Superficielles et Profondes",
             "promotion": "M1"},
            {"code_ue": "CIVI412", "intitule": "Béton Armé II : Eurocode 2 et Calcul des Structures Complexes",
             "promotion": "M1"},
            {"code_ue": "CIVI413", "intitule": "Charpente Métallique et Mixte (Eurocode 3)", "promotion": "M1"},
            {"code_ue": "CIVI414", "intitule": "Infrastructures Routières, Terrassement et Assainissement",
             "promotion": "M1"},
            {"code_ue": "CIVI511", "intitule": "Ouvrages d'Art : Ponts, Tunnels et Soutènements", "promotion": "M2"},
            {"code_ue": "CIVI512", "intitule": "Dynamique des Structures et Génie Parasismique", "promotion": "M2"},
            {"code_ue": "CIVI513", "intitule": "Planification, Plan de Charge et Management BIM", "promotion": "M2"},
            {"code_ue": "CIVI514", "intitule": "Projet de Fin d'Études (PFE) et Synthèse Technique", "promotion": "M2"},
        ],
        "Architecture": [
            {"code_ue": "ARCH111", "intitule": "Introduction à l'Architecture et Théorie des Formes",
             "promotion": "L1"},
            {"code_ue": "ARCH112", "intitule": "Dessin Technique, Géométrie Descriptive et Perspective",
             "promotion": "L1"},
            {"code_ue": "ARCH113", "intitule": "Histoire Générale de l'Art et de l'Espace Bâti", "promotion": "L1"},
            {"code_ue": "ARCH114", "intitule": "Physique du Bâtiment : Lumière et Acoustique Initiale",
             "promotion": "L1"},
            {"code_ue": "ARCH211", "intitule": "Atelier de Projet Architectural I : La Cellule et l'Habitat",
             "promotion": "L2"},
            {"code_ue": "ARCH212", "intitule": "RDM (Résistance des Matériaux) appliquée aux Structures",
             "promotion": "L2"},
            {"code_ue": "ARCH213", "intitule": "Matériaux de Construction traditionnels et modernes",
             "promotion": "L2"},
            {"code_ue": "ARCH214", "intitule": "Topographie et Relevé de l'Édifice", "promotion": "L2"},
            {"code_ue": "ARCH311", "intitule": "Atelier de Projet Architectural II : Équipements Publics Épurés",
             "promotion": "L3"},
            {"code_ue": "ARCH312", "intitule": "Technologie du Bâtiment et Systèmes Constructifs", "promotion": "L3"},
            {"code_ue": "ARCH313", "intitule": "Urbanisme et Morphologie Urbaine", "promotion": "L3"},
            {"code_ue": "ARCH314", "intitule": "CAO/DAO : Modélisation 2D/3D (AutoCAD / Revit)", "promotion": "L3"},
            {"code_ue": "ARCH411", "intitule": "Conception Architecturale Complexe et Structures Spéciales",
             "promotion": "M1"},
            {"code_ue": "ARCH412", "intitule": "Architecture Bioclimatique et Haute Qualité Environnementale (HQE)",
             "promotion": "M1"},
            {"code_ue": "ARCH413", "intitule": "Restauration, Réhabilitation et Patrimoine Bâti", "promotion": "M1"},
            {"code_ue": "ARCH414", "intitule": "Sociologie Urbaine et Politiques de l'Habitat", "promotion": "M1"},
            {"code_ue": "ARCH511", "intitule": "Projet de Fin d'Études (PFE) : Grand Équipement", "promotion": "M2"},
            {"code_ue": "ARCH512", "intitule": "Pratique Professionnelle, Déontologie et Droit de la Construction",
             "promotion": "M2"},
            {"code_ue": "ARCH513", "intitule": "Économie de la Construction, Métré et Cahier des Charges",
             "promotion": "M2"},
            {"code_ue": "ARCH514", "intitule": "Management de Projet et BIM", "promotion": "M2"},
        ],
        "Pétrole et Gaz": [
            {"code_ue": "PETR111", "intitule": "Algèbre et Analyse pour l'Ingénieur", "promotion": "L1"},
            {"code_ue": "PETR112", "intitule": "Chimie Organique et Solutions Aqueuses", "promotion": "L1"},
            {"code_ue": "PETR113", "intitule": "Géologie Générale et Cristallographie", "promotion": "L1"},
            {"code_ue": "PETR114", "intitule": "Introduction à l'Industrie Pétrolière (Amont/Aval)", "promotion": "L1"},
            {"code_ue": "PETR211", "intitule": "Thermodynamique Chimique et Transfert de Chaleur", "promotion": "L2"},
            {"code_ue": "PETR212", "intitule": "Mécanique des Fluides Incompressibles", "promotion": "L2"},
            {"code_ue": "PETR213", "intitule": "Géologie Structurale et Sédimentologie", "promotion": "L2"},
            {"code_ue": "PETR214", "intitule": "Informatique Numérique et Programmation (Python)", "promotion": "L2"},
            {"code_ue": "PETR311", "intitule": "Ingénierie des Réservoirs I : Propriétés des Roches",
             "promotion": "L3"},
            {"code_ue": "PETR312", "intitule": "Techniques de Forage et Complétion des Puits", "promotion": "L3"},
            {"code_ue": "PETR313", "intitule": "Évaluation des Formations (Diagraphies)", "promotion": "L3"},
            {"code_ue": "PETR314", "intitule": "Géophysique d'Exploration et Sismique Réflexion", "promotion": "L3"},
            {"code_ue": "PETR411", "intitule": "Ingénierie des Réservoirs II : Simulation Numérique",
             "promotion": "M1"},
            {"code_ue": "PETR412", "intitule": "Production du Pétrole et du Gaz : Activation et Collecte",
             "promotion": "M1"},
            {"code_ue": "PETR413", "intitule": "Raffinage du Pétrole et Pétrochimie", "promotion": "M1"},
            {"code_ue": "PETR414", "intitule": "Sécurité Industrielle, HSE et Risques Technologiques",
             "promotion": "M1"},
            {"code_ue": "PETR511", "intitule": "Économie Pétrolière, Droit Minier et Contrats Partagés",
             "promotion": "M2"},
            {"code_ue": "PETR512", "intitule": "Traitement du Gaz Naturel et GNL", "promotion": "M2"},
            {"code_ue": "PETR513", "intitule": "Transport, Stockage et Logistique des Hydrocarbures",
             "promotion": "M2"},
            {"code_ue": "PETR514", "intitule": "Gestion des Champs Matures et Abandon des Puits", "promotion": "M2"},
        ],
        "Informatique": [
            {"code_ue": "INFO111", "intitule": "Algorithmique Fondamentale et Structures de Données",
             "promotion": "L1"},
            {"code_ue": "INFO112", "intitule": "Introduction à l'Architecture des Ordinateurs et SE",
             "promotion": "L1"},
            {"code_ue": "INFO113", "intitule": "Logique Mathématique et Algèbre de Boole", "promotion": "L1"},
            {"code_ue": "INFO114", "intitule": "Programmation Impérative (Langage C / Python)", "promotion": "L1"},
            {"code_ue": "INFO211", "intitule": "Programmation Orientée Objet (Java / C++)", "promotion": "L2"},
            {"code_ue": "INFO212", "intitule": "Systèmes de Gestion de Bases de Données (SQL)", "promotion": "L2"},
            {"code_ue": "INFO213", "intitule": "Réseaux Informatiques : Modèles OSI et TCP/IP", "promotion": "L2"},
            {"code_ue": "INFO214", "intitule": "Développement Web Front-End (HTML, CSS, JS)", "promotion": "L2"},
            {"code_ue": "INFO311", "intitule": "Génie Logiciel : Conception UML et Design Patterns", "promotion": "L3"},
            {"code_ue": "INFO312", "intitule": "Développement Web Avancé (Framework Django)", "promotion": "L3"},
            {"code_ue": "INFO313", "intitule": "Administration Systèmes et Sécurité Réseaux (Linux)",
             "promotion": "L3"},
            {"code_ue": "INFO314", "intitule": "Recherche Opérationnelle et Optimisation Linéaire", "promotion": "L3"},
            {"code_ue": "INFO411", "intitule": "Intelligence Artificielle et Machine Learning", "promotion": "M1"},
            {"code_ue": "INFO412", "intitule": "Systèmes Distribués, Cloud Computing et Virtualisation",
             "promotion": "M1"},
            {"code_ue": "INFO413", "intitule": "Bases de Données NoSQL et Écosystème Big Data", "promotion": "M1"},
            {"code_ue": "INFO414", "intitule": "Cryptographie et Sécurité des Systèmes d'Information",
             "promotion": "M1"},
            {"code_ue": "INFO511", "intitule": "Architecture Microservices, Devops et CI/CD", "promotion": "M2"},
            {"code_ue": "INFO512", "intitule": "Internet des Objets (IoT) et Systèmes Embarqués", "promotion": "M2"},
            {"code_ue": "INFO513", "intitule": "Gestion de Projets Agiles (Scrum) et Audit", "promotion": "M2"},
            {"code_ue": "INFO514", "intitule": "Vision par Ordinateur et Traitement du Langage (NLP)",
             "promotion": "M2"},
        ],
        "Économie": [
            {"code_ue": "ECON111", "intitule": "Introduction à la Microéconomie", "promotion": "L1"},
            {"code_ue": "ECON112", "intitule": "Introduction à la Macroéconomie", "promotion": "L1"},
            {"code_ue": "ECON113", "intitule": "Histoire des Faits Économiques", "promotion": "L1"},
            {"code_ue": "ECON114", "intitule": "Mathématiques Appliquées à l'Économie", "promotion": "L1"},
            {"code_ue": "ECON211", "intitule": "Microéconomie Intermédiaire : Équilibre de Marché", "promotion": "L2"},
            {"code_ue": "ECON212", "intitule": "Macroéconomie Intermédiaire : Modèles IS-LM", "promotion": "L2"},
            {"code_ue": "ECON213", "intitule": "Statistique Descriptive et Calcul des Probabilités", "promotion": "L2"},
            {"code_ue": "ECON214", "intitule": "Comptabilité Générale et Analyse Financière", "promotion": "L2"},
            {"code_ue": "ECON311", "intitule": "Économétrie I : Régression Linéaire", "promotion": "L3"},
            {"code_ue": "ECON312", "intitule": "Économie Monétaire, Bancaire et Marchés Financiers", "promotion": "L3"},
            {"code_ue": "ECON313", "intitule": "Économie Internationale : Échanges", "promotion": "L3"},
            {"code_ue": "ECON314", "intitule": "Économie du Développement et Croissance", "promotion": "L3"},
            {"code_ue": "ECON411", "intitule": "Économétrie II : Séries Temporelles", "promotion": "M1"},
            {"code_ue": "ECON412", "intitule": "Économie Industrielle, Régulation et Théorie des Jeux",
             "promotion": "M1"},
            {"code_ue": "ECON413", "intitule": "Évaluation Économique des Projets d'Investissement", "promotion": "M1"},
            {"code_ue": "ECON414", "intitule": "Économie Publique et Analyse des Politiques Fiscales",
             "promotion": "M1"},
            {"code_ue": "ECON511", "intitule": "Macroéconomie Avancée et Modélisation", "promotion": "M2"},
            {"code_ue": "ECON512", "intitule": "Finance Internationale et Gestion des Risques", "promotion": "M2"},
            {"code_ue": "ECON513", "intitule": "Théorie des Incitations et Régulation Économique", "promotion": "M2"},
            {"code_ue": "ECON514", "intitule": "Mémoire de Recherche ou Stage de Fin d'Études", "promotion": "M2"},
        ]
    }
    return render(request, 'core/catalogue.html', {'catalogue': DATA_PROGRAMMES})