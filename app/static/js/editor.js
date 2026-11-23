/**
 * WYSIWYG Markdown editor (Toast UI Editor) for notes, wiki pages and task
 * descriptions. Optional: data-md-height="360px" on the textarea.
 *
 * Every <textarea data-md-editor> is replaced by an editor. The textarea
 * stays in the form (hidden) and receives the Markdown right before the
 * form is submitted, so the server simply receives plain Markdown text.
 * Users can switch between the WYSIWYG view and the raw Markdown view with
 * the tabs at the bottom of the editor.
 */
(function () {
  "use strict";

  function init() {
    if (!window.toastui) {
      // Editor script failed to load: fall back to the plain textarea.
      document.querySelectorAll("textarea[data-md-editor]").forEach((t) => {
        t.classList.remove("d-none");
        t.rows = 20;
      });
      return;
    }

    document.querySelectorAll("textarea[data-md-editor]").forEach((textarea) => {
      const container = document.createElement("div");
      textarea.insertAdjacentElement("afterend", container);

      const editor = new toastui.Editor({
        el: container,
        initialValue: textarea.value,
        initialEditType: "wysiwyg",
        previewStyle: "vertical",
        height: textarea.dataset.mdHeight || "560px",
        language: "nl-NL",
        usageStatistics: false,  // do not send usage data to the vendor
        toolbarItems: [
          ["heading", "bold", "italic", "strike"],
          ["hr", "quote"],
          ["ul", "ol", "task", "indent", "outdent"],
          ["table", "link", "image"],
          ["code", "codeblock"],
        ],
      });

      textarea.form.addEventListener("submit", () => {
        textarea.value = editor.getMarkdown();
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
