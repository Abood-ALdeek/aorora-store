(function () {
  "use strict";
  var KEY = "aorora_cart_v1";
  var A = window.AORORA || { t: {}, lang: "en", whatsapp: "" };
  var t = A.t;

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; }
  }
  function save(cart) {
    try { localStorage.setItem(KEY, JSON.stringify(cart)); } catch (e) {}
    updateCount();
  }
  function count() {
    var c = load(), n = 0;
    for (var k in c) n += c[k];
    return n;
  }
  function updateCount() {
    var n = count();
    document.querySelectorAll("[data-cart-count]").forEach(function (el) {
      el.textContent = n;
      el.hidden = n === 0;
    });
  }
  function fmt(n) {
    // Jordanian dinar: up to 3 decimals; 35 -> "35", 24.5 -> "24.50"
    var s = (Math.round(n * 1000) / 1000).toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
    if (/\.\d$/.test(s)) s += "0";
    return s + " " + t.currency;
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var toastEl;
  function toast(msg) {
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "toast";
      toastEl.setAttribute("role", "status");
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toastEl._t);
    toastEl._t = setTimeout(function () { toastEl.classList.remove("show"); }, 1800);
  }

  function add(id, qty) {
    var cart = load();
    cart[id] = Math.min(20, (cart[id] || 0) + qty);
    save(cart);
    toast("✓ " + t.added);
    var btn = document.querySelector(".cart-btn");
    if (btn) { btn.classList.remove("bump"); void btn.offsetWidth; btn.classList.add("bump"); }
  }

  document.addEventListener("click", function (e) {
    var addBtn = e.target.closest("[data-add]");
    if (addBtn) {
      e.preventDefault();
      var qty = 1;
      if (addBtn.hasAttribute("data-with-qty")) {
        var inp = document.querySelector("[data-qty] input");
        qty = Math.max(1, Math.min(20, parseInt(inp && inp.value, 10) || 1));
      }
      add(addBtn.getAttribute("data-add"), qty);
      return;
    }
    var step = e.target.closest("[data-qty] [data-step]");
    if (step) {
      var input = step.parentElement.querySelector("input");
      var v = (parseInt(input.value, 10) || 1) + parseInt(step.getAttribute("data-step"), 10);
      input.value = Math.max(1, Math.min(20, v));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });

  // image preview in admin forms
  document.querySelectorAll("[data-preview-input]").forEach(function (input) {
    input.addEventListener("change", function () {
      var f = input.files && input.files[0];
      var box = input.closest(".photo-field").querySelector("[data-preview]");
      if (!f || !box) return;
      var url = URL.createObjectURL(f);
      box.innerHTML = "";
      var img = document.createElement("img");
      img.src = url;
      box.appendChild(img);
    });
  });

  document.querySelectorAll("[data-year]").forEach(function (el) { el.textContent = new Date().getFullYear(); });
  updateCount();

  // ---------- cart page ----------
  function renderCartPage() {
    var itemsEl = document.querySelector("[data-cart-items]");
    var full = document.querySelector("[data-cart-full]");
    var empty = document.querySelector("[data-cart-empty]");
    var totalEl = document.querySelector("[data-cart-total]");
    var form = document.querySelector("[data-order-form]");
    var products = {};

    function draw() {
      var cart = load();
      var ids = Object.keys(cart).filter(function (id) { return products[id]; });
      // drop items that no longer exist
      Object.keys(cart).forEach(function (id) { if (!products[id]) delete cart[id]; });
      try { localStorage.setItem(KEY, JSON.stringify(cart)); } catch (e) {}
      updateCount();

      if (!ids.length) { full.hidden = true; empty.hidden = false; return; }
      full.hidden = false; empty.hidden = true;
      var total = 0;
      itemsEl.innerHTML = ids.map(function (id) {
        var p = products[id], q = cart[id];
        if (p.in_stock) total += p.price * q;
        return '<div class="cart-item" data-id="' + id + '">' +
          '<a href="/product/' + id + '">' + (p.image ? '<img src="' + esc(p.image) + '" alt="">' : '<img alt="">') + '</a>' +
          '<div><a class="ci-name" href="/product/' + id + '">' + esc(p.name) + '</a>' +
          '<span class="price">' + fmt(p.price) + '</span>' +
          (p.in_stock ? '' : '<div class="ci-unavailable">' + esc(t.out_of_stock) + '</div>') + '</div>' +
          '<div class="ci-side">' +
            '<div class="qty sm" data-qty><button type="button" data-step="-1">−</button>' +
            '<input type="number" min="1" max="20" value="' + q + '" aria-label="' + esc(t.qty) + '">' +
            '<button type="button" data-step="1">+</button></div>' +
            '<button type="button" class="link-btn small" data-remove>' + esc(t.remove) + '</button>' +
          '</div></div>';
      }).join("");
      totalEl.textContent = fmt(total);
    }

    itemsEl.addEventListener("change", function (e) {
      var row = e.target.closest(".cart-item");
      if (!row || e.target.tagName !== "INPUT") return;
      var cart = load();
      cart[row.getAttribute("data-id")] = Math.max(1, Math.min(20, parseInt(e.target.value, 10) || 1));
      save(cart); draw();
    });
    itemsEl.addEventListener("click", function (e) {
      if (!e.target.closest("[data-remove]")) return;
      var row = e.target.closest(".cart-item");
      var cart = load();
      delete cart[row.getAttribute("data-id")];
      save(cart); draw();
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var f = form.elements;
      var d = {
        name: f["name"].value.trim(), phone: f["phone"].value.trim(),
        address: f["address"].value.trim(), note: f["note"].value.trim()
      };
      var err = form.querySelector("[data-form-error]");
      if (err) err.hidden = true;  // all details are optional

      var cart = load(), lines = [t.wa_greeting, ""], total = 0, n = 1;
      Object.keys(cart).forEach(function (id) {
        var p = products[id];
        if (!p || !p.in_stock) return;
        var q = cart[id];
        total += p.price * q;
        var label = p.name + (A.lang === "ar" && p.name_alt ? " (" + p.name_alt + ")" : "");
        lines.push(n++ + ". " + label + " × " + q + " = " + fmt(p.price * q));
        // link to the product page: WhatsApp shows its photo as a preview
        lines.push("   " + t.wa_photo + ": " + String(A.siteUrl || location.origin).replace(/\/$/, "") + "/product/" + id);
      });
      if (n === 1) { draw(); return; }
      lines.push("", "*" + t.wa_total + ": " + fmt(total) + "*");
      if (d.name || d.phone || d.address || d.note) lines.push("");
      if (d.name) lines.push(t.wa_name + ": " + d.name);
      if (d.phone) lines.push(t.wa_phone + ": " + d.phone);
      if (d.address) lines.push(t.wa_address + ": " + d.address);
      if (d.note) lines.push(t.wa_note + ": " + d.note);

      var url = "https://wa.me/" + String(A.whatsapp).replace(/\D/g, "") + "?text=" + encodeURIComponent(lines.join("\n"));
      window.location.href = url;
    });

    fetch("/api/products", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (list) {
        list.forEach(function (p) { products[p.id] = p; });
        draw();
      })
      .catch(function () { empty.hidden = false; });
  }

  window.AORORA_CART = { renderCartPage: renderCartPage };
})();
