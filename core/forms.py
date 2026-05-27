from django import forms
from django.contrib.auth.models import User
from .models import Profile, Filiere, PROMO_CHOICES  # Importation correcte ici


class InscriptionForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Mot de passe'}),
        label="Mot de passe"
    )
    password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirmez le mot de passe'}),
        label="Confirmation du mot de passe"
    )

    filiere = forms.ModelChoiceField(
        queryset=Filiere.objects.all(),
        widget=forms.Select(attrs={'class': 'form-control'}),
        label="Votre Filière",
        empty_label="-- Choisissez votre filière --"
    )

    # CORRECTION : Utilisation de la variable globale PROMO_CHOICES
    promotion = forms.ChoiceField(
        choices=PROMO_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'}),
        label="Votre Année / Promotion"
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control', 'placeholder': "Nom d'utilisateur"}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': "Adresse email"}),
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': "Prénom"}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': "Nom"}),
        }

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and User.objects.filter(email=email).exists():
            raise forms.ValidationError("Cette adresse email est déjà utilisée.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        password_confirm = cleaned_data.get("password_confirm")

        if password and password_confirm and password != password_confirm:
            raise forms.ValidationError("Les mots de passe ne correspondent pas.")
        return cleaned_data

    # AJOUT IMPORTANT : Sauvegarde du Profile
    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
            # Création du profil lié au user
            Profile.objects.create(
                user=user,
                filiere=self.cleaned_data["filiere"],
                promotion=self.cleaned_data["promotion"]
            )
        return user