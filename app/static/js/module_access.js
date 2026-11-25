// User form: access per module. Write includes read: ticking "write" ticks
// "read", unticking "read" unticks "write". For administrators the boxes are
// disabled (they may do everything).
(function () {
  const box = document.querySelector("[data-module-access]");
  if (!box) return;
  const form = box.closest("form");
  const role = form.elements.role;

  function rows() {
    return [...box.querySelectorAll("tbody tr")].map((tr) => ({
      read: tr.querySelector("[data-access-read]"), write: tr.querySelector("[data-access-write]"),
    }));
  }
  function updateRole() {
    const admin = role && role.value === "admin";
    box.querySelectorAll("input, [data-access-all]").forEach((el) => { el.disabled = admin; });
    box.classList.toggle("opacity-50", admin);
  }
  box.addEventListener("change", (event) => {
    const tr = event.target.closest("tr");
    if (!tr) return;
    const read = tr.querySelector("[data-access-read]"), write = tr.querySelector("[data-access-write]");
    if (event.target === write && write.checked) read.checked = true;
    if (event.target === read && !read.checked) write.checked = false;
  });
  box.addEventListener("click", (event) => {
    const button = event.target.closest("[data-access-all]");
    if (!button) return;
    const level = button.dataset.accessAll;
    rows().forEach(({ read, write }) => {
      read.checked = level !== "none";
      write.checked = level === "write";
    });
  });
  if (role) role.addEventListener("change", updateRole);
  updateRole();
})();
