/* OmniSport - menú que se contrae y cambio de tema claro/oscuro. Sin dependencias. */
(function () {
  "use strict";
  var raiz = document.documentElement;

  // ---- Tema claro / oscuro ----
  var botonTema = document.getElementById("tema");
  function aplicarTema(tema) {
    raiz.setAttribute("data-tema", tema);
    if (botonTema) {
      botonTema.setAttribute("aria-pressed", tema === "oscuro" ? "true" : "false");
      botonTema.setAttribute("title", tema === "oscuro" ? "Cambiar a modo claro" : "Cambiar a modo oscuro");
    }
  }
  aplicarTema(raiz.getAttribute("data-tema") === "oscuro" ? "oscuro" : "claro");
  if (botonTema) {
    botonTema.addEventListener("click", function () {
      var nuevo = raiz.getAttribute("data-tema") === "oscuro" ? "claro" : "oscuro";
      aplicarTema(nuevo);
      try { localStorage.setItem("tema", nuevo); } catch (e) { /* sin almacenamiento: el cambio vale solo para esta visita */ }
    });
  }

  // ---- Menú principal (hamburguesa en pantallas chicas) ----
  var botonMenu = document.getElementById("boton-menu");
  var menu = document.getElementById("menu-principal");
  function cerrarMenu() {
    if (!menu || !botonMenu) return;
    menu.classList.remove("abierto");
    botonMenu.setAttribute("aria-expanded", "false");
  }
  if (botonMenu && menu) {
    botonMenu.addEventListener("click", function () {
      var abierto = menu.classList.toggle("abierto");
      botonMenu.setAttribute("aria-expanded", abierto ? "true" : "false");
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && menu.classList.contains("abierto")) { cerrarMenu(); botonMenu.focus(); }
    });
  }
})();
