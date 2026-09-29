/**
 * 编辑页的极简富文本工具栏：把选中的文本用 HTML 标签包起来。
 * 每个 .rt-toolbar 通过 data-target 指向它要操作的 textarea id。
 */
(function () {
    function wrapSelection(textarea, tag, attr) {
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        if (start === end) {
            return;
        }
        const selected = textarea.value.substring(start, end);
        const open = attr ? `<${tag} ${attr}>` : `<${tag}>`;
        textarea.setRangeText(`${open}${selected}</${tag}>`, start, end, "select");
        textarea.focus();
    }

    document.querySelectorAll(".rt-toolbar").forEach((toolbar) => {
        const textarea = document.getElementById(toolbar.dataset.target);
        if (!textarea) {
            return;
        }

        const fontSize = toolbar.querySelector(".rt-fontsize");
        if (fontSize) {
            fontSize.addEventListener("change", function () {
                if (this.value) {
                    wrapSelection(textarea, "span", `style="font-size:${this.value}"`);
                }
            });
        }

        const color = toolbar.querySelector(".rt-color");
        if (color) {
            color.addEventListener("input", function () {
                wrapSelection(textarea, "span", `style="color:${this.value}"`);
            });
        }

        const bold = toolbar.querySelector(".rt-bold");
        if (bold) {
            bold.addEventListener("click", () => wrapSelection(textarea, "b"));
        }

        toolbar.querySelectorAll(".rt-tag").forEach((button) => {
            button.addEventListener("click", function () {
                wrapSelection(textarea, this.dataset.tag);
            });
        });
    });
})();
