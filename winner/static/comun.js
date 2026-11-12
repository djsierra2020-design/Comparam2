// Utilidades compartidas por las pantallas de Winner.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const pesos = n => (n < 0 ? "-" : "") + "$" + Math.abs(Math.round(n || 0)).toLocaleString("es-CO");
const miles = n => Math.round(n || 0).toLocaleString("es-CO");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const hora = f => f ? new Date(f).toLocaleString("es-CO", {hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short"}) : "";

async function api(ruta, metodo = "GET", datos) {
  const r = await fetch("/winner/api" + ruta, {
    method: metodo, credentials: "same-origin",
    headers: {"Content-Type": "application/json", "X-Winner": "1"},
    body: datos === undefined ? undefined : JSON.stringify(datos),
  });
  let cuerpo = {};
  try { cuerpo = await r.json(); } catch (e) { /* sin cuerpo */ }
  if (!r.ok) {
    const err = new Error(cuerpo.error || "Error " + r.status);
    err.status = r.status;
    throw err;
  }
  return cuerpo;
}

let _toastT;
function aviso(texto, error = false) {
  const t = $("#toast");
  t.textContent = texto;
  t.className = error ? "error" : "";
  t.style.display = "block";
  clearTimeout(_toastT);
  _toastT = setTimeout(() => { t.style.display = "none"; }, error ? 6000 : 3500);
}

// Ejecuta una acción mostrando el error de negocio tal cual y evitando el doble clic.
async function accion(fn, boton) {
  if (boton) boton.disabled = true;
  try { return await fn(); }
  catch (e) { aviso(e.message, true); if (e.status === 401 && window.alSalir) window.alSalir(); }
  finally { if (boton) boton.disabled = false; }
}

// Diálogo con formulario. campos: [{n, l, tipo, valor, opciones}]
function pedir(titulo, campos, textoBoton = "Aceptar", detalle = "") {
  return new Promise(resolve => {
    const d = document.createElement("dialog");
    d.innerHTML = `<h3>${esc(titulo)}</h3>${detalle}<form method="dialog">` + campos.map(c => c.tipo === "select"
      ? `<label>${esc(c.l)}</label><select name="${c.n}">${c.opciones.map(o => `<option value="${esc(o[0])}">${esc(o[1])}</option>`).join("")}</select>`
      : c.tipo === "check" ? `<label style="display:flex;gap:8px;align-items:center;color:var(--texto)"><input type="checkbox" name="${c.n}" style="width:auto"> ${esc(c.l)}</label>`
      : `<label>${esc(c.l)}</label><input name="${c.n}" type="${c.tipo || "text"}" value="${esc(c.valor ?? "")}" ${c.tipo === "number" ? 'inputmode="numeric"' : ""} autocomplete="off">`
    ).join("") + `<div class="fila" style="margin-top:14px;justify-content:flex-end"><button value="no" class="sec" formnovalidate>Cancelar</button><button value="si">${esc(textoBoton)}</button></div></form>`;
    document.body.appendChild(d);
    d.addEventListener("close", () => {
      const datos = {};
      $$("[name]", d).forEach(i => { datos[i.name] = i.type === "checkbox" ? i.checked : i.value; });
      resolve(d.returnValue === "si" ? datos : null);
      d.parentNode.removeChild(d);
    });
    d.showModal();
    const primero = $("input,select", d);
    if (primero) primero.focus();
  });
}

async function confirmar(titulo, detalle = "", textoBoton = "Confirmar") {
  return (await pedir(titulo, [], textoBoton, detalle ? `<p>${detalle}</p>` : "")) !== null;
}
