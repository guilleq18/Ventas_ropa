from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission

from core.models import Sucursal
from catalogo.models import Categoria
from .models import UsuarioPerfil


PROJECT_PERMISSION_APPS = [
    "admin_panel",
    "caja",
    "catalogo",
    "core",
    "cuentas_corrientes",
    "ventas",
]


class AdminPanelUserForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Contraseña",
        required=False,
        widget=forms.PasswordInput(render_value=False),
    )
    password2 = forms.CharField(
        label="Repetir contraseña",
        required=False,
        widget=forms.PasswordInput(render_value=False),
    )
    sucursal = forms.ModelChoiceField(
        queryset=Sucursal.objects.filter(activa=True).order_by("nombre"),
        required=False,
        empty_label="Sin sucursal asignada",
    )
    groups = forms.ModelMultipleChoiceField(
        label="Roles",
        queryset=Group.objects.all().order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
    )

    class Meta:
        model = get_user_model()
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
        ]
        widgets = {
            "is_active": forms.CheckboxInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in ("username", "first_name", "last_name", "email"):
            self.fields[field].widget.attrs.setdefault("class", "validate")

        if self.instance and self.instance.pk:
            profile = getattr(self.instance, "panel_profile", None)
            if profile is not None:
                self.fields["sucursal"].initial = profile.sucursal_id
            self.fields["groups"].initial = self.instance.groups.all()

    def clean(self):
        cleaned = super().clean()
        p1 = (cleaned.get("password1") or "").strip()
        p2 = (cleaned.get("password2") or "").strip()

        creating = not bool(self.instance and self.instance.pk)

        if creating and not p1:
            self.add_error("password1", "La contraseña es obligatoria para crear el usuario.")
        if p1 or p2:
            if p1 != p2:
                self.add_error("password2", "Las contraseñas no coinciden.")
            elif len(p1) < 6:
                self.add_error("password1", "Usá al menos 6 caracteres.")

        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        password = (self.cleaned_data.get("password1") or "").strip()
        if password:
            user.set_password(password)

        if commit:
            user.save()
            user.groups.set(self.cleaned_data.get("groups") or [])
            profile, _ = UsuarioPerfil.objects.get_or_create(user=user)
            profile.sucursal = self.cleaned_data.get("sucursal")
            profile.save()

        return user


class RoleForm(forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        label="Permisos",
        queryset=Permission.objects.select_related("content_type")
        .filter(content_type__app_label__in=PROJECT_PERMISSION_APPS)
        .order_by("content_type__app_label", "codename"),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
    )

    class Meta:
        model = Group
        fields = ["name", "permissions"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].label = "Nombre del rol"
        self.fields["name"].widget.attrs.setdefault("class", "validate")


class AdminPanelCategoriaForm(forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ["nombre", "activa"]
        widgets = {
            "nombre": forms.TextInput(attrs={"autocomplete": "off", "class": "validate"}),
            "activa": forms.CheckboxInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["nombre"].label = "Nombre"
        self.fields["activa"].label = "Activa"
