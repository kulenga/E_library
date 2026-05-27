from django.contrib import admin
from .models import Filiere, CoursProgramme, Support, Profile, ReferencePartagee, SalonDeDiscussion, InvitationSalon

# Enregistrez chaque modèle une seule fois
admin.site.register(Filiere)
admin.site.register(CoursProgramme)
admin.site.register(Support)
admin.site.register(Profile)
admin.site.register(ReferencePartagee)
admin.site.register(SalonDeDiscussion)
admin.site.register(InvitationSalon)