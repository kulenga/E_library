from django.contrib.auth import views as auth_views
from django.urls import path, register_converter
from . import views

# Convertisseur pour les actions d'invitation
class ActionInvitationConverter:
    regex = 'accepter|refuser'
    def to_python(self, value): return value
    def to_url(self, value): return value

register_converter(ActionInvitationConverter, 'action_invit')

urlpatterns = [
    # --- AUTHENTIFICATION & ACCUEIL ---
    path('', views.home_publique, name='home_publique'),
    path('inscription/', views.inscription, name='inscription'),
    path('deconnexion/', auth_views.LogoutView.as_view(next_page='home_publique'), name='deconnexion'),
    path('dashboard/', views.dashboard, name='dashboard'),

    # --- ACADÉMIQUE & PROGRAMMES ---
    path('programme/', views.voir_programme, name='voir_programme'),
    path('administration/initialiser-cours/', views.initialiser_les_cours, name='initialiser_cours'),

    # --- ESPACES DE DISCUSSION (SALONS) ---
    path('salons/', views.gestion_salons, name='gestion_salons'),
    path('salons/<int:salon_id>/', views.voir_salon, name='voir_salon'),
    path('salons/<int:salon_id>/api/', views.api_messages, name='api_messages'),
    path('salons/<int:salon_id>/inviter/', views.envoyer_invitation, name='envoyer_invitation'),
    path('invitations/<int:invitation_id>/<action_invit:action>/', views.repondre_invitation, name='repondre_invitation'),

    # --- ASSISTANT IA ---
    path('chat-ia/', views.chat_ia_view, name='chat_ia'),
    path('catalogue/', views.voir_catalogue_complet, name='voir_catalogue_complet'),
]