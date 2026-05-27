@login_required
def gestion_salons(request):
    """Vue pour afficher et administrer les espaces de discussion de l'étudiant."""
    # OPTIMISATION : On charge l'utilisateur et son profil pour éviter une requête SQL masquée
    user_with_profile = User.objects.select_related('profile').get(id=request.user.id)
    profile = user_with_profile.profile

    if request.method == 'POST' and 'creer_salon' in request.POST:
        nom_salon = request.POST.get('nom_salon', '').strip()
        if nom_salon:
            with transaction.atomic():
                nouveau_salon = SalonDeDiscussion.objects.create(nom=nom_salon, createur=request.user)
                nouveau_salon.membres.add(request.user)
            messages.success(request, f"Le salon '{nom_salon}' a été créé avec succès.")
            return redirect('gestion_salons')
        messages.error(request, "Le nom du salon ne peut pas être vide.")

    mes_salons = SalonDeDiscussion.objects.filter(
        Q(createur=request.user) | Q(membres=request.user)
    ).select_related('createur').prefetch_related('membres').distinct()

    invitations_recues = InvitationSalon.objects.filter(
        invite=request.user,
        accepte__isnull=True
    ).select_related('hote', 'salon')

    camarades = User.objects.filter(
        profile__filiere=profile.filiere,
        is_active=True
    ).select_related('profile').exclude(id=request.user.id).only(
        'id', 'username', 'first_name', 'last_name',
        'profile__id', 'profile__user_id', 'profile__filiere'
    )

    invitations_en_cours = InvitationSalon.objects.filter(
        salon__in=mes_salons,
        accepte__isnull=True
    ).values('salon_id', 'invite_id')

    invitations_map = {}
    for inv in invitations_en_cours:
        invitations_map.setdefault(inv['salon_id'], set()).add(inv['invite_id'])

    membres_par_salon_map = {}
    for salon in mes_salons:
        salon.camarades_invitables = None
        if salon.createur_id == request.user.id:
            membres_par_salon_map[salon.id] = {m.id for m in salon.membres.all()}

    for salon in mes_salons:
        if salon.createur_id == request.user.id:
            membres_ids = membres_par_salon_map.get(salon.id, set())
            invites_en_attente_ids = invitations_map.get(salon.id, set())
            exclus_ids = membres_ids.union(invites_en_attente_ids)

            salon.camarades_invitables = camarades.exclude(id__in=exclus_ids)

    # Contexte corrigé et enrichi
    context = {
        'mes_salons': mes_salons,  # Correction de la faute de frappe ici
        'invitations_recues': invitations_recues,
        'camarades': camarades,
        'invitations_map': invitations_map,  # Ajouté au cas où le template en dépend
    }
    return render(request, 'core/salons.html', context)