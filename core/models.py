from django.db import models
from django.contrib.auth.models import User
from django.core.validators import FileExtensionValidator

# --- 1. CONFIGURATION GLOBALE ---
PROMO_CHOICES = [
    ('L1', 'Licence 1'), ('L2', 'Licence 2'), ('L3', 'Licence 3'),
    ('M1', 'Master 1'), ('M2', 'Master 2'),
]

# --- 2. FILIÈRES ---
class Filiere(models.Model):
    nom = models.CharField(max_length=100, unique=True, db_index=True)

    class Meta:
        verbose_name = "Filière"
        verbose_name_plural = "Filières"
        ordering = ['nom']

    def __str__(self):
        return self.nom

# --- 3. STRUCTURE ACADÉMIQUE ---
class CoursProgramme(models.Model):
    filiere = models.ForeignKey(Filiere, on_delete=models.CASCADE, related_name='cours_programme')
    promotion = models.CharField(max_length=2, choices=PROMO_CHOICES, db_index=True)
    intitule = models.CharField(max_length=200, db_index=True)
    code_ue = models.CharField(max_length=20, blank=True, db_index=True)

    class Meta:
        verbose_name = "Cours au programme"
        verbose_name_plural = "Cours au programme"
        ordering = ['promotion', 'intitule']
        unique_together = ['filiere', 'promotion', 'intitule']

    def __str__(self):
        return f"{self.intitule} ({self.promotion} - {self.filiere.nom})"

# --- 4. UTILISATEURS ---
class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    filiere = models.ForeignKey(Filiere, on_delete=models.SET_NULL, null=True, blank=True)
    promotion = models.CharField(max_length=2, choices=PROMO_CHOICES, db_index=True)

    class Meta:
        verbose_name = "Profil Étudiant"
        verbose_name_plural = "Profils Étudiants"

    def __str__(self):
        filiere_nom = self.filiere.nom if self.filiere else "Sans filière"
        return f"{self.user.username} - {filiere_nom} ({self.promotion})"

# --- 5. SUPPORTS (Documents) ---
class Support(models.Model):
    cours = models.ForeignKey(
        CoursProgramme,
        on_delete=models.CASCADE,
        related_name='supports',
        null=True,
        blank=True,
        db_index=True
    )
    titre = models.CharField(max_length=200, db_index=True)
    fichier = models.FileField(
        upload_to='supports/',
        validators=[FileExtensionValidator(['pdf', 'docx', 'pptx'])]
    )
    exemples = models.TextField(blank=True)
    exercices = models.TextField(blank=True)
    fichier_exercices = models.FileField(
        upload_to='supports/exercices/',
        blank=True,
        validators=[FileExtensionValidator(['pdf'])]
    )

    class Meta:
        verbose_name = "Support de cours"
        verbose_name_plural = "Supports de cours"
        ordering = ['titre']

    def __str__(self):
        return self.titre

# --- 6. ENTRAIDE ET SOCIAL ---
class ReferencePartagee(models.Model):
    etudiant = models.ForeignKey(User, on_delete=models.CASCADE, related_name='references_partagees')
    titre = models.CharField(max_length=200, db_index=True)
    description_ou_lien = models.TextField()
    filiere = models.ForeignKey(Filiere, on_delete=models.CASCADE, related_name='references_filieres')
    promotion = models.CharField(max_length=2, choices=PROMO_CHOICES, db_index=True)
    date_publication = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Ressource partagée"
        verbose_name_plural = "Ressources partagées"
        ordering = ['-date_publication']

    def __str__(self):
        return f"{self.titre} (par {self.etudiant.username})"

class SalonDeDiscussion(models.Model):
    nom = models.CharField(max_length=100)
    createur = models.ForeignKey(User, on_delete=models.CASCADE, related_name="salons_crees")
    membres = models.ManyToManyField(User, related_name="salons_rejoints")
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Salon de discussion"
        verbose_name_plural = "Salons de discussion"
        ordering = ['-date_creation']

    def __str__(self):
        return self.nom

class MessageSalon(models.Model):
    """Nouveau modèle requis pour le stockage des échanges au sein des salons d'études."""
    salon = models.ForeignKey(SalonDeDiscussion, on_delete=models.CASCADE, related_name='messages_contenus')
    auteur = models.ForeignKey(User, on_delete=models.CASCADE, related_name='messages_salons')
    texte = models.TextField()
    date_envoi = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Message de salon"
        verbose_name_plural = "Messages de salons"
        ordering = ['date_envoi']

    def __str__(self):
        return f"Msg de {self.auteur.username} dans {self.salon.nom} ({self.date_envoi.strftime('%H:%M')})"

class InvitationSalon(models.Model):
    salon = models.ForeignKey(SalonDeDiscussion, on_delete=models.CASCADE, related_name="invitations")
    hote = models.ForeignKey(User, on_delete=models.CASCADE, related_name="invitations_envoyees")
    invite = models.ForeignKey(User, on_delete=models.CASCADE, related_name="invitations_recues")
    accepte = models.BooleanField(null=True, blank=True, db_index=True)
    date_invitation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Invitation salon"
        verbose_name_plural = "Invitations salons"
        # Empêche d'envoyer plusieurs invitations actives identiques au même utilisateur pour le même salon
        unique_together = ['salon', 'hote', 'invite', 'accepte']

    def __str__(self):
        status = "En attente" if self.accepte is None else ("Acceptée" if self.accepte else "Refusée")
        return f"Invitation de {self.hote.username} pour {self.invite.username} ({status})"