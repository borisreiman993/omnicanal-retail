from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.forms import formset_factory

from .models import Devolucion, OperacionCompra, Perfil, Producto, Sucursal, Venta

User = get_user_model()


class LoginForm(forms.Form):
    username = forms.CharField(label="Usuario", max_length=150)
    password = forms.CharField(label="Contraseña", widget=forms.PasswordInput)


class RegistroForm(UserCreationForm):
    """Autoregistro: SIEMPRE crea un Cliente. Los perfiles internos se crean con un mecanismo tecnico restringido."""

    first_name = forms.CharField(label="Nombre", max_length=60)
    last_name = forms.CharField(label="Apellido", max_length=60)
    email = forms.EmailField(label="Correo electrónico", help_text="Aquí recibirás las notificaciones de tus pedidos.")

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email")
        labels = {"username": "Usuario"}

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Ya existe una cuenta con este correo.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            Perfil.objects.create(user=user, rol=Perfil.CLIENTE)
        return user


class ProductoForm(forms.ModelForm):
    class Meta:
        model = Producto
        fields = ["sku", "nombre", "descripcion", "precio", "umbral_stock_critico", "activo"]
        labels = {"sku": "Código (SKU)", "precio": "Precio (CLP)", "umbral_stock_critico": "Umbral de stock crítico", "activo": "Activo (visible para la venta)"}
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 3})}

    def clean_precio(self):
        precio = self.cleaned_data["precio"]
        if precio <= 0:
            raise forms.ValidationError("El precio debe ser mayor que cero.")
        return precio


class AjusteForm(forms.Form):
    nuevo_stock = forms.IntegerField(label="Stock registrado contado físicamente", min_value=0)
    motivo = forms.CharField(label="Motivo del ajuste", max_length=200)


class ItemVentaForm(forms.Form):
    producto = forms.ModelChoiceField(queryset=Producto.objects.none(), required=False, label="Producto")
    cantidad = forms.IntegerField(min_value=1, required=False, label="Cantidad")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["producto"].queryset = Producto.objects.filter(activo=True)

    def clean(self):
        datos = super().clean()
        if bool(datos.get("producto")) != bool(datos.get("cantidad")):
            raise forms.ValidationError("Completa el producto y la cantidad de la fila.")
        return datos


ItemVentaFormSet = formset_factory(ItemVentaForm, extra=6)


class VentaExtraForm(forms.Form):
    cliente_nombre = forms.CharField(label="Nombre del cliente (opcional)", max_length=120, required=False)
    requiere_bodega = forms.BooleanField(label="Requiere preparación o entrega por Bodega (genera aviso)", required=False, initial=True)


class AgregarCarritoForm(forms.Form):
    producto_id = forms.IntegerField(widget=forms.HiddenInput)
    cantidad = forms.IntegerField(min_value=1, initial=1)


class CheckoutForm(forms.Form):
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.none(), label="Sucursal que atenderá la compra", empty_label="Selecciona una sucursal")
    modalidad = forms.ChoiceField(choices=OperacionCompra.MODALIDADES, label="Modalidad de entrega", widget=forms.RadioSelect, initial=OperacionCompra.RETIRO)
    direccion = forms.CharField(label="Dirección de despacho", max_length=250, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sucursal"].queryset = Sucursal.objects.filter(activa=True)


class BuscarVentaForm(forms.Form):
    venta_id = forms.IntegerField(label="N° de la venta original", min_value=1)


class DevolucionForm(forms.Form):
    canal_solicitud = forms.ChoiceField(choices=Venta.CANALES, label="Canal por el que se presenta la solicitud")
    fundamento = forms.ChoiceField(choices=Devolucion.FUNDAMENTOS, label="Fundamento")
    motivo = forms.CharField(label="Motivo", widget=forms.Textarea(attrs={"rows": 3}))
    antecedentes = forms.CharField(label="Antecedentes (falla invocada, estado del producto, etc.)", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    fecha_recepcion_cliente = forms.DateField(label="Fecha de recepción del producto", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    confirmacion_escrita = forms.BooleanField(label="Se envió la confirmación escrita de la contratación (solo retracto)", required=False, initial=True)


class EvaluacionForm(forms.Form):
    DECISIONES = [(Devolucion.APROBADA, "Aprobar"), (Devolucion.RECHAZADA, "Rechazar"),
                  (Devolucion.PENDIENTE, "Dejar pendiente (faltan antecedentes)")]
    decision = forms.ChoiceField(choices=DECISIONES, label="Decisión")
    justificacion = forms.CharField(label="Justificación", widget=forms.Textarea(attrs={"rows": 3}), required=False)


class RecepcionForm(forms.Form):
    cantidad_recibida = forms.IntegerField(label="Unidades recibidas", min_value=1)
    cantidad_apta = forms.IntegerField(label="De ellas, aptas para venta", min_value=0)
    observacion = forms.CharField(label="Observación", max_length=200, required=False)


class OperadorForm(forms.Form):
    nombre = forms.CharField(label="Operador logístico", max_length=120)
    contacto = forms.CharField(label="Contacto", max_length=120, required=False)
    referencia = forms.CharField(label="N° de seguimiento", max_length=80, required=False)
