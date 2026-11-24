// Time registration form (popup and page): field service shows the customer
// and kilometres; the customer follows the project, the kilometres follow the
// customer's distance from the office (there and back).
(function () {
  function update(form, source) {
    var box = form.querySelector('[data-time-form]');
    if (!box) return;
    var fs = form.elements.field_service, project = form.elements.project_id,
        customer = form.elements.customer_id, km = form.elements.km;
    box.querySelectorAll('[data-field-service]').forEach(function (el) {
      el.classList.toggle('d-none', !fs.checked);
    });
    if (!fs.checked) return;
    if ((source === 'project' || source === 'field_service') && customer.value === '0') {
      var opt = project.options[project.selectedIndex];
      var cid = opt && opt.dataset.customer;
      if (cid && customer.querySelector('option[value="' + cid + '"]')) customer.value = cid;
    }
    var copt = customer.options[customer.selectedIndex];
    var dist = copt ? copt.dataset.distance : '';
    var note = box.querySelector('[data-distance-note]');
    if (note) note.textContent = dist ? 'Afstand kantoor - klant: ' + dist.replace('.', ',') + ' km enkel' :
      (customer.value !== '0' ? 'Geen afstand gekend voor deze klant (vul in bij de relatie).' : '');
    if (dist && (source === 'customer' || ((source === 'project' || source === 'field_service') && !km.value))) {
      km.value = (Math.round(parseFloat(dist) * 20) / 10).toString();
    }
  }
  function init(root) {
    (root || document).querySelectorAll('[data-time-form]').forEach(function (box) {
      update(box.closest('form'), 'init');
    });
  }
  document.addEventListener('change', function (e) {
    var form = e.target.form;
    if (!form || !form.querySelector('[data-time-form]')) return;
    var name = e.target.name;
    if (name === 'field_service') update(form, 'field_service');
    else if (name === 'project_id') update(form, 'project');
    else if (name === 'customer_id') update(form, 'customer');
  });
  document.addEventListener('DOMContentLoaded', function () { init(); });
  document.addEventListener('modal:loaded', function (e) { init(e.target); });
})();
