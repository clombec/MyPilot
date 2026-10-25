"""
urls.py

Routes of the web interface.
"""

from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("capture/", views.capture, name="capture"),
    path("observation/", views.quick_note, name="quick_note"),
    path("taches/", views.task_list, name="task_list"),
    path("taches/<int:task_id>/", views.task_detail, name="task_detail"),
    path("taches/<int:task_id>/fait/", views.task_done, name="task_done"),
    path("notes/", views.note_list, name="note_list"),
    path("notes/nouvelle/", views.note_edit, name="note_create"),
    path("notes/<int:note_id>/", views.note_edit, name="note_edit"),
    path("connexion/", auth_views.LoginView.as_view(), name="login"),
    path("deconnexion/", auth_views.LogoutView.as_view(), name="logout"),
]
