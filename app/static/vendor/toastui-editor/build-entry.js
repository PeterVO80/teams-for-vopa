// Bundles Toast UI Editor + its ProseMirror dependencies + Dutch UI texts
// into one browser script exposing `window.toastui.Editor`.
import Editor from "@toast-ui/editor";
import "@toast-ui/editor/dist/i18n/nl-nl";
window.toastui = { Editor };
