/**
 * FPGA Register Debug Tool - 前端主逻辑
 */

// ==================== 页面初始化 ====================
// API 路径前缀，由服务端模板注入
const APP = window.APP_CONFIG || { base: "", chip: "", module: "" };
const APP_BASE = APP.base || "";
const API_BASE = APP.chip && APP.module
    ? `${APP_BASE}/api/${APP.chip}/${APP.module}`
    : `${APP_BASE}/api`;

document.addEventListener("DOMContentLoaded", () => {
    // 无编辑权限时隐藏编辑相关按钮
    if (!APP.can_edit) {
        const editButtons = document.querySelectorAll("#btn-add-register, #btn-import-regdef, #input-regdef-file, #btn-show-json-example");
        editButtons.forEach(b => b.style.display = "none");
        // 隐藏表格中的操作列
        document.querySelectorAll("#regdef-table th:last-child, #regdef-table td:last-child").forEach(c => c.style.display = "none");
    }
    refreshModuleList();
    // 自动加载当前模块（有下拉框时选中，无下拉框时直接加载）
    if (APP.module) {
        const sel = document.getElementById("select-module");
        if (sel) {
            // 等模块列表加载完后自动选中当前模块
            setTimeout(() => {
                if (sel.querySelector(`option[value="${APP.module}"]`)) {
                    sel.value = APP.module;
                    loadModuleRegisters(APP.module);
                }
            }, 300);
        } else {
            loadModuleRegisters(APP.module);
        }
    }
    bindEvents();
});

// ==================== 全局状态 ====================
let _latestParseData = null;    // 最近一次解析的完整数据
let _currentRegisters = [];     // 当前加载的寄存器原始列表（用于搜索过滤）
let _editOriginalAddress = "";  // 修改寄存器时的原始地址（用于删除旧地址）
let _rawResults = { abnormal: [], defined: [], all: [] };  // 原始解析结果（用于结果搜索）
let _resultTables = {};  // 保存创建的表格引用，防止 DOM 查询失败

// ==================== 工具函数 ====================
function $(selector) {
    return document.querySelector(selector);
}

function $$(selector) {
    return document.querySelectorAll(selector);
}

/**
 * 格式化字段值：单 bit 显示 0/1，多 bit 显示十六进制
 */
function formatFieldValue(val, width) {
    if (width === 1) return String(val);
    const hexWidth = Math.ceil(width / 4);
    return "0x" + val.toString(16).toUpperCase().padStart(hexWidth, "0");
}

// ==================== 事件绑定 ====================
function bindEvents() {
    // 寄存器录入按钮
    $("#btn-add-register").addEventListener("click", openRegisterModal);
    $("#btn-modal-close").addEventListener("click", closeRegisterModal);
    $("#btn-cancel-register").addEventListener("click", closeRegisterModal);
    $("#btn-save-register").addEventListener("click", saveRegister);
    // 地址输入框失焦时自动补齐预览
    $("#reg-address").addEventListener("blur", function() {
        const modName = $("#reg-module-name").value.trim() || APP.module || "";
        const completed = autoCompleteAddress(this.value.trim(), modName);
        if (completed !== this.value.trim()) {
            this.value = completed;
        }
    });

    // 点击遮罩关闭
    $("#modal-register").addEventListener("click", function(e) {
        if (e.target === this) closeRegisterModal();
    });

    // 添加 bit 字段行
    $("#btn-add-bitfield").addEventListener("click", addBitfieldRow);

    // 模块选择切换
    const selectModule = $("#select-module");
    if (selectModule) {
        selectModule.addEventListener("change", function() {
            const moduleName = this.value;
            if (moduleName === "__ALL__") {
                loadAllModules();
            } else if (moduleName) {
                loadModuleRegisters(moduleName);
            } else {
                clearRegdefTable();
            }
        });
    }

    // JSON示例
    $("#btn-show-json-example").addEventListener("click", showJsonExample);
    $("#btn-json-example-close").addEventListener("click", closeJsonExample);
    $("#btn-json-example-cancel").addEventListener("click", closeJsonExample);
    $("#btn-copy-json").addEventListener("click", copyJsonExample);
    $("#modal-json-example").addEventListener("click", function(e) {
        if (e.target === this) closeJsonExample();
    });

    // 导入寄存器定义
    $("#btn-import-regdef").addEventListener("click", function() {
        $("#input-regdef-file").click();
    });
    $("#input-regdef-file").addEventListener("change", importRegdefFile);

    // Dump 文件上传
    $("#input-dump-files").addEventListener("change", function() {
        const files = this.files;
        if (files.length > 0) {
            $("#dump-files-info").textContent = Array.from(files).map(f => f.name).join(", ");
        } else {
            $("#dump-files-info").textContent = "未选择文件";
        }
    });

    // 解析按钮
    $("#btn-parse").addEventListener("click", parseDumpFiles);

    // 三类导出按钮
    $("#btn-export-all").addEventListener("click", exportAllExcel);
    $("#btn-export-defined").addEventListener("click", exportDefinedExcel);
    $("#btn-export-abnormal").addEventListener("click", exportAbnormalExcel);

    // 寄存器搜索
    $("#regdef-search").addEventListener("input", filterRegdefTable);
    $("#btn-regdef-search").addEventListener("click", filterRegdefTable);

    // 解析结果搜索（动态绑定）
    document.addEventListener("input", function(e) {
        if (e.target.classList.contains("result-search")) {
            filterResultTable(e.target.dataset.target);
        }
    });
}

// ==================== 模块列表管理 ====================
async function refreshModuleList() {
    try {
        const resp = await fetch(`${API_BASE}/modules`);
        const data = await resp.json();
        const modules = data.modules || [];

        // 更新下拉框（配置检查页面可能没有下拉框）
        const select = $("#select-module");
        if (!select) return;
        select.innerHTML = '<option value="">-- 选择模块查看 --</option>';
        if (modules.length > 1) {
            const allOpt = document.createElement("option");
            allOpt.value = "__ALL__";
            allOpt.textContent = "ALL — 查看全部模块";
            select.appendChild(allOpt);
        }
        modules.forEach(m => {
            const opt = document.createElement("option");
            opt.value = m;
            opt.textContent = m;
            select.appendChild(opt);
        });

        // 更新 datalist（用于录入弹窗的自动补全）
        const datalist = $("#module-datalist");
        datalist.innerHTML = "";
        modules.forEach(m => {
            const opt = document.createElement("option");
            opt.value = m;
            datalist.appendChild(opt);
        });
    } catch (e) {
        console.error("获取模块列表失败", e);
    }
}

// ==================== 寄存器定义表格 ====================
async function loadAllModules() {
    try {
        const resp = await fetch(`${API_BASE}/modules`);
        const data = await resp.json();
        const modules = data.modules || [];
        _currentRegisters = [];
        for (const m of modules) {
            const r = await fetch(`${API_BASE}/module/${encodeURIComponent(m)}`);
            const d = await r.json();
            for (const reg of (d.registers || [])) {
                // 给每个寄存器名加模块前缀以区分，同时保留原始模块名供编辑/删除使用
                _currentRegisters.push({ ...reg, name: `[${m}] ${reg.name}`, _origModule: m });
            }
        }
        _currentRegisters.sort((a, b) => {
            const addrA = parseInt(a.address_offset, 16) || 0;
            const addrB = parseInt(b.address_offset, 16) || 0;
            return addrA - addrB;
        });
        renderRegdefTable(_currentRegisters);
    } catch (e) {
        console.error("加载全部寄存器失败", e);
    }
}

async function loadModuleRegisters(moduleName) {
    try {
        const resp = await fetch(`${API_BASE}/module/${encodeURIComponent(moduleName)}`);
        const data = await resp.json();
        _currentRegisters = data.registers || [];

        // 按地址偏移从小到大排列
        _currentRegisters.sort((a, b) => {
            const addrA = parseInt(a.address_offset, 16) || 0;
            const addrB = parseInt(b.address_offset, 16) || 0;
            return addrA - addrB;
        });

        renderRegdefTable(_currentRegisters);
    } catch (e) {
        console.error("加载寄存器定义失败", e);
    }
}

function renderRegdefTable(registers, moduleNameOverride) {
    const selectEl = $("#select-module");
    const modName = moduleNameOverride || (selectEl ? selectEl.value : "") || APP.module || "";
    const tbody = $("#regdef-table tbody");
    tbody.innerHTML = "";

    if (registers.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:#999;">无匹配的寄存器</td></tr>';
        return;
    }

    registers.forEach(reg => {
        const name = reg.name || "";
        const addr = reg.address_offset || "";
        const bitfields = reg.bitfields || [];

            if (bitfields.length === 0) {
                const tr = document.createElement("tr");
                const btnModule = reg._origModule || modName;
                tr.innerHTML = `
                    <td>${escapeHtml(name)}</td>
                    <td>${escapeHtml(addr)}</td>
                    <td>-</td>
                    <td>-</td>
                    <td>-</td>
                    <td>-</td>
                    <td>-</td>
                    <td>${escapeHtml(reg.updated_by || "-")}</td>
                    <td>
                        <button class="btn btn-sm btn-primary" onclick="editRegister('${escapeAttr(btnModule)}', '${escapeAttr(addr)}')">修改</button>
                        <button class="btn btn-sm btn-danger" onclick="deleteRegister('${escapeAttr(btnModule)}', '${escapeAttr(addr)}')">删除</button>
                    </td>
                `;
                tbody.appendChild(tr);
                return;
            }

            // 按 start_bit 升序排列，最低位在前
            const sorted = [...bitfields].sort((a, b) => a.start_bit - b.start_bit);
            const regId = `reg-${modName}-${addr}`.replace(/[^a-zA-Z0-9-]/g, '');

            sorted.forEach((bf, idx) => {
                const start = bf.start_bit;
                const end = bf.end_bit;
                const bitRange = start === end ? `[${start}]` : `[${end}:${start}]`;
                const fieldName = bf.field_name || "";
                const expectedVal = bf.expected_value || "-";
                const abnormalVal = bf.abnormal_value || "-";
                const enums = bf.enums || [];
                let enumStr = "";
                if (enums.length > 0) {
                    enumStr = enums.map(e => `${e.value}: ${e.meaning}`).join(", ");
                }

                const isFirst = idx === 0;
                const isExtra = idx > 0; // 折叠隐藏的行

                const tr = document.createElement("tr");
                tr.className = `reg-group-${regId}`;
                if (isExtra) tr.style.display = "none";

                const btnModule = reg._origModule || modName;

                tr.innerHTML = `
                    <td>${isFirst ? `<span class="debug-toggle" onclick="toggleRegGroup('${regId}', this)">+</span> ${escapeHtml(name)}` : ""}</td>
                    <td>${isFirst ? escapeHtml(addr) : ""}</td>
                    <td>${bitRange}</td>
                    <td>${escapeHtml(fieldName)}</td>
                    <td>${escapeHtml(expectedVal)}</td>
                    <td>${escapeHtml(abnormalVal)}</td>
                    <td>${escapeHtml(enumStr)}</td>
                    <td>${isFirst ? escapeHtml(reg.updated_by || "-") : ""}</td>
                    <td>
                        ${isFirst ? `<button class="btn btn-sm btn-primary" onclick="editRegister('${escapeAttr(btnModule)}', '${escapeAttr(addr)}')">修改</button>
                        <button class="btn btn-sm btn-danger" onclick="deleteRegister('${escapeAttr(btnModule)}', '${escapeAttr(addr)}')">删除</button>` : ""}
                    </td>
                `;
                tbody.appendChild(tr);
            });
        });
}

function filterRegdefTable() {
    const keyword = ($("#regdef-search").value || "").trim().toLowerCase();
    if (!keyword) {
        renderRegdefTable(_currentRegisters);
        return;
    }
    const filtered = _currentRegisters.filter(reg => {
        // 匹配地址偏移
        if (reg.address_offset && reg.address_offset.toLowerCase().includes(keyword)) return true;
        // 匹配寄存器名
        if (reg.name && reg.name.toLowerCase().includes(keyword)) return true;
        // 匹配字段名
        if (reg.bitfields) {
            for (const bf of reg.bitfields) {
                if (bf.field_name && bf.field_name.toLowerCase().includes(keyword)) return true;
            }
        }
        return false;
    });
    renderRegdefTable(filtered);
}

function filterResultTable(target) {
    if (!_latestParseData) return;
    const keyword = (document.querySelector(`.result-search[data-target="${target}"]`)?.value || "").trim().toLowerCase();
    const source = _rawResults[target] || [];
    if (!keyword) {
        // 重新渲染全部
        const dumpNames = _latestParseData?.dump_names || [];
        if (target === "abnormal") renderAbnormalResultTable("abnormal-table-head", "abnormal-table-body", source, dumpNames);
        else if (target === "defined") renderResultTable("defined-table-head", "defined-table-body", source, dumpNames);
        else renderResultTable("result-table-head", "result-table-body", source, dumpNames);
        return;
    }
    const filtered = source.filter(r => {
        if (r.address_offset && r.address_offset.toLowerCase().includes(keyword)) return true;
        if (r.reg_name && r.reg_name.toLowerCase().includes(keyword)) return true;
        if (r.field_name && r.field_name.toLowerCase().includes(keyword)) return true;
        if (r.module && r.module.toLowerCase().includes(keyword)) return true;
        return false;
    });
    const dumpNames = _latestParseData?.dump_names || [];
    if (target === "abnormal") renderAbnormalResultTable("abnormal-table-head", "abnormal-table-body", filtered, dumpNames);
    else if (target === "defined") renderResultTable("defined-table-head", "defined-table-body", filtered, dumpNames);
    else renderResultTable("result-table-head", "result-table-body", filtered, dumpNames);
}

function clearRegdefTable() {
    _currentRegisters = [];
    const tbody = $("#regdef-table tbody");
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:#999;">请选择一个模块</td></tr>';
    $("#regdef-search").value = "";
}

// ==================== 展开/折叠 ====================

// 寄存器定义表格：单级展开
function toggleRegGroup(regId, toggleEl) {
    const rows = document.querySelectorAll(`.reg-group-${regId}`);
    const isExpanded = toggleEl.textContent === "-";
    rows.forEach((row, i) => {
        if (i > 0) row.style.display = isExpanded ? "none" : "";
    });
    toggleEl.textContent = isExpanded ? "+" : "-";
}

// 解析结果表格：模块级展开（显示该模块下所有寄存器摘要行）
function toggleResGroup(modId, lev, toggleEl) {
    const rows = document.querySelectorAll(`.rmod-group-${modId}`);
    const isExpanded = toggleEl.textContent === "-";
    rows.forEach((row, i) => {
        if (i > 0) row.style.display = isExpanded ? "none" : "";
    });
    toggleEl.textContent = isExpanded ? "+" : "-";
}

// 解析结果表格：寄存器级展开（显示该寄存器下所有bitfield行）
function toggleResReg(modId, regId, toggleEl) {
    const rows = document.querySelectorAll(`.rreg-group-${regId}`);
    const isExpanded = toggleEl.textContent === "-";
    rows.forEach(row => {
        row.style.display = isExpanded ? "none" : "";
    });
    toggleEl.textContent = isExpanded ? "+" : "-";
}

// ==================== 删除寄存器 ====================
async function deleteRegister(moduleName, addressOffset) {
    if (!confirm(`确认删除模块 "${moduleName}" 地址 "${addressOffset}" 的寄存器定义？`)) return;

    try {
        const resp = await fetch(`${API_BASE}/register`, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ module_name: moduleName, address_offset: addressOffset }),
        });
        const data = await resp.json();
        if (data.error) {
            alert("删除失败: " + data.error);
            return;
        }
        loadModuleRegisters(moduleName);
        refreshModuleList();
    } catch (e) {
        alert("删除失败: " + e.message);
    }
}

// ==================== 修改寄存器 ====================
async function editRegister(moduleName, addressOffset) {
    try {
        const resp = await fetch(`${API_BASE}/module/${encodeURIComponent(moduleName)}`);
        const data = await resp.json();
        const registers = data.registers || [];
        // 标准化地址比较（大小写不敏感，统一 trim 并转大写）
        const normalizedAddr = (addressOffset || "").trim().toUpperCase();
        const reg = registers.find(r => (r.address_offset || "").trim().toUpperCase() === normalizedAddr);
        if (!reg) {
            const availAddrs = registers.map(r => r.address_offset).join(", ");
            console.error("editRegister: 未找到匹配寄存器", { moduleName, addressOffset, normalizedAddr, availAddrs, registersCount: registers.length });
            alert(`未找到该寄存器定义！\n模块: ${moduleName}\n查找地址: ${addressOffset}\n模块中寄存器数量: ${registers.length}\n已有地址: ${availAddrs || "(无)"}`);
            return;
        }

        // 预填弹窗，记录原始地址用于后续删除旧记录
        _editOriginalAddress = reg.address_offset || "";
        $("#reg-module-name").value = moduleName;
        $("#reg-name").value = reg.name || "";
        $("#reg-address").value = reg.address_offset || "";

        // 清空现有 bitfield 行
        const container = $("#bitfield-rows");
        container.innerHTML = "";

        // 按寄存器定义逐行填充
        const bitfields = reg.bitfields || [];
        bitfields.forEach(bf => {
            addBitfieldRow();
            const row = container.lastElementChild;
            // 设置值
            row.querySelector(".bf-start-bit").value = bf.start_bit;
            row.querySelector(".bf-end-bit").value = bf.end_bit;
            row.querySelector(".bf-field-name").value = bf.field_name || "";

            // 正常值
            const expectedInput = row.querySelector(".bf-expected-value");
            if (expectedInput) {
                expectedInput.value = bf.expected_value || "";
            }
            const abnormalInput = row.querySelector(".bf-abnormal-value");
            if (abnormalInput) {
                abnormalInput.value = bf.abnormal_value || "";
            }

            // 枚举
            const enums = bf.enums || [];
            if (enums.length > 0) {
                const enumRows = row.querySelector(".enum-rows");
                const enumToggle = row.querySelector(".bitfield-enum-toggle");
                enumRows.style.display = "block";
                enumToggle.textContent = "- 收起枚举";
                enumToggle.style.display = "inline";
                // 清空默认的空行
                enumRows.innerHTML = "";
                enums.forEach(e => {
                    const enumRow = document.createElement("div");
                    enumRow.className = "enum-row";
                    enumRow.innerHTML = `
                        <input type="text" class="enum-value-input" value="${escapeHtml(e.value || '')}" style="width:80px;">
                        <span>:</span>
                        <input type="text" class="enum-meaning-input" value="${escapeHtml(e.meaning || '')}" style="flex:1;">
                        <button class="btn btn-sm btn-danger" onclick="this.parentElement.remove()">×</button>
                    `;
                    enumRows.appendChild(enumRow);
                });
            }

            // 触发校验（枚举开关显隐）
            const start = parseInt(row.querySelector(".bf-start-bit").value);
            const end = parseInt(row.querySelector(".bf-end-bit").value);
            const toggle = row.querySelector(".bitfield-enum-toggle");
            if (start !== end) {
                toggle.style.display = "inline";
            }
        });

        // 如果没有 bitfield，默认加一行
        if (bitfields.length === 0) {
            addBitfieldRow();
        }

        // 直接显示弹窗（不能用 openRegisterModal，它会清空已填数据）
        $("#modal-register").style.display = "flex";
    } catch (e) {
        alert("加载寄存器失败: " + e.message);
    }
}

// ==================== 寄存器录入弹窗 ====================
function openRegisterModal() {
    // 清除编辑状态
    _editOriginalAddress = "";
    // 模块名字默认取 APP.module（如 IVDC），如果已有值（如 readonly 预填）则保留
    const modInput = $("#reg-module-name");
    if (!modInput.value) {
        modInput.value = APP.module || "";
    }
    $("#reg-name").value = "";
    $("#reg-address").value = "";
    $("#bitfield-rows").innerHTML = "";
    addBitfieldRow(); // 默认添加一行
    $("#modal-register").style.display = "flex";
}

function closeRegisterModal() {
    $("#modal-register").style.display = "none";
}

/**
 * 添加一行 bit 字段定义
 */
function addBitfieldRow() {
    const container = $("#bitfield-rows");
    const rowDiv = document.createElement("div");
    rowDiv.className = "bitfield-row";

    // 起始 bit select
    let startSelectHtml = '<select class="bf-start-bit">';
    for (let i = 0; i <= 31; i++) {
        startSelectHtml += `<option value="${i}">${i}</option>`;
    }
    startSelectHtml += '</select>';

    let endSelectHtml = '<select class="bf-end-bit">';
    for (let i = 0; i <= 31; i++) {
        endSelectHtml += `<option value="${i}" ${i === 3 ? 'selected' : ''}>${i}</option>`;
    }
    endSelectHtml += '</select>';

    rowDiv.innerHTML = `
        ${startSelectHtml}
        <span style="line-height:28px;">~</span>
        ${endSelectHtml}
        <input type="text" class="bf-field-name" placeholder="bit 字段含义" style="flex:1;">
        <input type="text" class="bf-expected-value" placeholder="0x1, 0x2" style="width:110px;" title="正常工作时的期望值，支持多个值用逗号/分号/竖线分隔">
        <input type="text" class="bf-abnormal-value" placeholder="异常值 (可选)" style="width:100px;" title="异常状态时的值，正常值和异常值至少填一个">
        <span class="bitfield-enum-toggle" style="display:none;">+ 枚举</span>
        <button class="btn-del-bitfield" title="删除此行">删除</button>
        <div class="enum-rows" style="display:none;"></div>
    `;

    const startSel = rowDiv.querySelector(".bf-start-bit");
    const endSel = rowDiv.querySelector(".bf-end-bit");
    const enumToggle = rowDiv.querySelector(".bitfield-enum-toggle");

    // 校验：start <= end
    function validate() {
        const start = parseInt(startSel.value);
        const end = parseInt(endSel.value);
        if (start > end) {
            // 显示错误提示
            let errEl = rowDiv.querySelector(".bf-error");
            if (!errEl) {
                errEl = document.createElement("span");
                errEl.className = "bf-error";
                rowDiv.appendChild(errEl);
            }
            errEl.textContent = "起始 bit 不能大于结束 bit";
            startSel.style.borderColor = "#dc3545";
            endSel.style.borderColor = "#dc3545";
        } else {
            const errEl = rowDiv.querySelector(".bf-error");
            if (errEl) errEl.remove();
            startSel.style.borderColor = "#ccc";
            endSel.style.borderColor = "#ccc";
        }
        // 多 bit 时显示枚举开关
        enumToggle.style.display = (start !== end) ? "inline" : "none";
    }

    startSel.addEventListener("change", validate);
    endSel.addEventListener("change", validate);

    // 枚举开关逻辑
    enumToggle.addEventListener("click", function() {
        const enumRows = rowDiv.querySelector(".enum-rows");
        if (enumRows.style.display === "none") {
            enumRows.style.display = "block";
            enumToggle.textContent = "- 收起枚举";
            // 如果还没有枚举行，添加一行
            if (enumRows.children.length === 0) {
                addEnumRow(enumRows);
            }
        } else {
            enumRows.style.display = "none";
            enumToggle.textContent = "+ 枚举";
        }
    });

    // 删除按钮
    rowDiv.querySelector(".btn-del-bitfield").addEventListener("click", function() {
        rowDiv.remove();
    });

    container.appendChild(rowDiv);
    validate();
}

/**
 * 在 enum-rows 容器中添加一行枚举输入
 */
function addEnumRow(container) {
    const row = document.createElement("div");
    row.className = "enum-row";
    row.innerHTML = `
        <input type="text" class="enum-value-input" placeholder="0x0" style="width:80px;">
        <span>:</span>
        <input type="text" class="enum-meaning-input" placeholder="含义" style="flex:1;">
        <button class="btn btn-sm btn-danger" onclick="this.parentElement.remove()">×</button>
    `;
    container.appendChild(row);
}

/**
 * 自动补齐寄存器地址：如果用户只填了偏移值（不足8位hex），拼接模块基地址
 */
function autoCompleteAddress(addr, moduleName) {
    // 统一加 0x 前缀
    let hexPart = addr.trim();
    if (hexPart.toLowerCase().startsWith("0x")) {
        hexPart = hexPart.substring(2);
    }
    if (!/^[0-9A-Fa-f]+$/.test(hexPart)) return addr;  // 非纯hex，原样返回
    if (hexPart.length >= 8) return "0x" + hexPart.toUpperCase();  // 已是完整地址

    // 从已有寄存器中查找参考地址（优先同模块，否则任意）
    let baseHex = "";
    for (const reg of _currentRegisters) {
        if (reg.address_offset) {
            const m = reg.address_offset.match(/^0x([0-9A-Fa-f]+)$/i);
            if (m && m[1].length >= 8) {
                baseHex = m[1];
                // 同模块优先，找到就停；否则继续找
                const origMod = reg._origModule || "";
                if (origMod === moduleName || !origMod) break;
            }
        }
    }
    if (!baseHex) return "0x" + hexPart.toUpperCase();  // 找不到参考地址，只规范格式

    // 用已有地址的高位 + 用户填的偏移
    const prefixLen = baseHex.length - hexPart.length;
    if (prefixLen <= 0) return "0x" + hexPart.toUpperCase();
    const fullHex = baseHex.substring(0, prefixLen) + hexPart.toUpperCase();
    return "0x" + fullHex;
}

/**
 * 保存寄存器
 */
async function saveRegister() {
    const moduleName = $("#reg-module-name").value.trim();
    const regName = $("#reg-name").value.trim();
    let address = $("#reg-address").value.trim();

    if (!moduleName || !regName || !address) {
        alert("模块名字、寄存器名字和地址偏移不能为空！");
        return;
    }

    // 自动补齐地址：如果用户只填了偏移值（不足8位hex），自动拼接模块基地址
    address = autoCompleteAddress(address, moduleName);

    // 收集 bitfields
    const bitfields = [];
    const rows = $$("#bitfield-rows .bitfield-row");
    for (const row of rows) {
        const start = parseInt(row.querySelector(".bf-start-bit").value);
        const end = parseInt(row.querySelector(".bf-end-bit").value);
        const fieldName = row.querySelector(".bf-field-name").value.trim();

        if (start > end) {
            alert("bit 字段起始 bit 不能大于结束 bit，请修正后再保存！");
            return;
        }
        if (!fieldName) {
            alert("bit 字段含义不能为空！");
            return;
        }

        const bf = {
            start_bit: start,
            end_bit: end,
            field_name: fieldName,
        };

        // 收集正常值（可选）
        const expectedVal = row.querySelector(".bf-expected-value")?.value?.trim();
        if (expectedVal) {
            bf.expected_value = expectedVal;
        }
        // 收集异常值（可选）
        const abnormalVal = row.querySelector(".bf-abnormal-value")?.value?.trim();
        if (abnormalVal) {
            bf.abnormal_value = abnormalVal;
        }
        // 正常值和异常值至少填一个（仅 IVDC 配置检查页面生效）
        if (APP.module === "IVDC" && !expectedVal && !abnormalVal) {
            alert(`字段 "${fieldName}" 的正常值和异常值不能同时为空，请至少填写一个！`);
            return;
        }

        // 收集枚举
        if (start !== end) {
            const enumRows = row.querySelectorAll(".enum-rows .enum-row");
            const enums = [];
            for (const er of enumRows) {
                const val = er.querySelector(".enum-value-input").value.trim();
                const meaning = er.querySelector(".enum-meaning-input").value.trim();
                if (val && meaning) {
                    enums.push({ value: val, meaning: meaning });
                }
            }
            if (enums.length > 0) {
                bf.enums = enums;
            }
        }

        bitfields.push(bf);
    }

    if (bitfields.length === 0) {
        alert("请至少添加一个 bit 字段定义！");
        return;
    }

    // 检查 bit 范围是否有交叉
    const sorted = [...bitfields].sort((a, b) => a.start_bit - b.start_bit);
    for (let i = 1; i < sorted.length; i++) {
        if (sorted[i].start_bit <= sorted[i - 1].end_bit) {
            alert(`Bit 范围交叉！字段 "${sorted[i-1].field_name}" [${sorted[i-1].end_bit}:${sorted[i-1].start_bit}] 与字段 "${sorted[i].field_name}" [${sorted[i].end_bit}:${sorted[i].start_bit}] 存在重叠，请修正后再保存！`);
            return;
        }
    }

    try {
        const resp = await fetch(`${API_BASE}/register`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                module_name: moduleName,
                reg_name: regName,
                address_offset: address,
                original_address_offset: _editOriginalAddress || address,
                bitfields: bitfields,
                updated_by: APP.user_name || "",
            }),
        });
        const data = await resp.json();
        if (data.error) {
            alert("保存失败: " + data.error);
            return;
        }
        closeRegisterModal();
        refreshModuleList();

        // 如果当前选中的模块就是刚编辑的模块，刷新表格
        const selEl = $("#select-module");
        const currentModule = selEl ? selEl.value : APP.module || "";
        if (currentModule === moduleName || !currentModule) {
            if (selEl) selEl.value = moduleName;
            loadModuleRegisters(moduleName);
        }

        alert("保存成功！");
    } catch (e) {
        alert("保存失败: " + e.message);
    }
}

// ==================== 导入寄存器定义 ====================
async function importRegdefFile() {
    const fileInput = $("#input-regdef-file");
    const file = fileInput.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    try {
        const resp = await fetch(`${API_BASE}/module/import`, {
            method: "POST",
            body: formData,
        });
        const data = await resp.json();
        if (data.error) {
            alert("导入失败: " + data.error);
            return;
        }
        alert(`成功导入 ${data.modules_saved.length} 个模块: ${data.modules_saved.join(", ")}`);
        refreshModuleList();
    } catch (e) {
        alert("导入失败: " + e.message);
    }
}

// ==================== Dump 文件解析 ====================
async function parseDumpFiles() {
    const fileInput = $("#input-dump-files");
    const files = fileInput.files;

    if (files.length === 0) {
        alert("请先选择 dump 文件！");
        return;
    }

    const formData = new FormData();
    for (const f of files) {
        formData.append("dump_files", f);
    }
    // 同时把当前所有已加载的 regdef 也传上去（服务器端已持久化，不需要重复上传）

    $("#btn-parse").textContent = "解析中...";
    $("#btn-parse").disabled = true;

    try {
        const resp = await fetch(`${API_BASE}/parse`, {
            method: "POST",
            body: formData,
        });
        const data = await resp.json();
        if (data.error) {
            alert("解析失败: " + data.error);
            return;
        }
        // 保存完整数据
        _latestParseData = data;
        renderResults(data);
    } catch (e) {
        alert("解析失败: " + e.message);
    } finally {
        $("#btn-parse").textContent = "解析";
        $("#btn-parse").disabled = false;
    }
}

/**
 * 渲染解析结果 —— 三段同时展示
 */
function renderResults(data) {
    // 确保结果区域存在
    let rs = document.getElementById("result-section");
    if (!rs) {
        console.error("result-section missing, recreating...");
        rs = document.createElement("div");
        rs.id = "result-section";
        rs.className = "debug-card";
        rs.style.display = "block";
        document.querySelector(".container")?.appendChild(rs);
    }

    const dumpNames = data.dump_names || [];
    const results = data.results || [];

    // 保存原始数据用于结果搜索
    _rawResults = {
        abnormal: results.filter(r => r.is_abnormal),
        defined: results.filter(r => r.reg_name !== "[未定义寄存器]"),
        all: results,
    };

    $("#result-section").style.display = "block";

    // 3.1 疑似异常点（特殊渲染：期望值列 + 红色高亮）
    const abnormalResults = _rawResults.abnormal || [];
    $("#section-abnormal").style.display = "block";
    if (abnormalResults.length > 0) {
        const regs = new Set(abnormalResults.map(r => `${r.module}:${r.address_offset}:${r.reg_name}`));
        $("#abnormal-summary").textContent = `共 ${abnormalResults.length} 个异常 bit 字段，涉及 ${regs.size} 个寄存器`;
        renderAbnormalResultTable("abnormal-table-head", "abnormal-table-body", abnormalResults, dumpNames);
    } else {
        $("#abnormal-summary").textContent = "暂未发现异常";
        document.getElementById("abnormal-table-head").innerHTML = "";
        document.getElementById("abnormal-table-body").innerHTML = "";
    }

    // 3.2 已定义的寄存器
    const definedResults = results.filter(r => r.reg_name !== "[未定义寄存器]");
    const definedRegs = new Set(definedResults.map(r => `${r.module}:${r.address_offset}:${r.reg_name}`));
    $("#defined-summary").textContent = `共 ${definedResults.length} 条记录，${definedRegs.size} 个已定义寄存器`;
    renderResultTable("defined-table-head", "defined-table-body", definedResults, dumpNames);

    // 3.3 全部寄存器
    const allRegs = new Set(results.map(r => `${r.module}:${r.address_offset}:${r.reg_name}`));
    $("#all-summary").textContent = `共 ${results.length} 条记录，${allRegs.size} 个寄存器（含未定义）`;
    renderResultTable("result-table-head", "result-table-body", results, dumpNames);

    // IVDC 配置检查：异常值后加跳转链接
    if (window.IVDC_STEP_MAP && window.IVDC_GUIDE_URL) {
        setTimeout(() => {
            document.querySelectorAll("#abnormal-table-body td, #defined-table-body td, #result-table-body td").forEach(td => {
                if (!td.classList.contains("debug-cell-abnormal")) return;
                const row = td.closest("tr");
                const addrCell = row?.querySelectorAll("td")[1];
                if (!addrCell) return;
                const addrText = addrCell.textContent.trim();
                const step = window.IVDC_STEP_MAP[addrText];
                if (step) {
                    const link = document.createElement("a");
                    link.href = window.IVDC_GUIDE_URL + "#step-" + encodeURIComponent(step);
                    link.textContent = " 📖";
                    link.title = "跳转到配置指导：" + step;
                    link.style.cssText = "text-decoration:none;font-size:14px;";
                    td.appendChild(link);
                }
            });
        }, 100);
    }

    // 启用导出按钮
    $("#btn-export-all").disabled = false;
    $("#btn-export-defined").disabled = false;
    $("#btn-export-abnormal").disabled = false;
}

/**
 * 渲染单个表格（两级折叠：模块 > 寄存器 > bit字段）
 */
function findOrCreateTable(tableId) {
    // 使用缓存引用
    if (_resultTables[tableId]) {
        const t = _resultTables[tableId];
        if (t.isConnected) return t;
    }
    let table = document.getElementById(tableId);
    if (table) { _resultTables[tableId] = table; return table; }
    // 动态创建
    const section = document.getElementById("result-section");
    if (!section) { console.error("No result-section!"); return null; }
    const wrap = document.createElement("div");
    wrap.className = "debug-table-wrap";
    table = document.createElement("table");
    table.id = tableId;
    table.className = "debug-table";
    table.innerHTML = "<thead></thead><tbody></tbody>";
    wrap.appendChild(table);
    section.appendChild(wrap);
    _resultTables[tableId] = table;
    return table;
}

function renderResultTable(theadId, tbodyId, results, dumpNames) {
    const table = findOrCreateTable(theadId.replace("-head",""));
    if (!table) return;
    const thead = table.querySelector("thead");
    const tbody = table.querySelector("tbody");

    thead.innerHTML = "";
    tbody.innerHTML = "";
    const headerRow = document.createElement("tr");
    headerRow.innerHTML = `
        <th>模块</th>
        <th>地址偏移</th>
        <th>寄存器名</th>
        <th>Bit范围</th>
        <th>字段名</th>
        ${dumpNames.map(n => `<th>${escapeHtml(n)} 值</th>`).join("")}
        ${dumpNames.length > 1 ? '<th>变化</th>' : ''}
        <th>枚举含义</th>
    `;
    thead.appendChild(headerRow);

    tbody.innerHTML = "";

    // 按 模块 > 寄存器地址 > bit起始位 三级分组
    const modules = {};
    results.forEach(item => {
        const m = item.module;
        const rkey = item.address_offset + "|" + item.reg_name;
        if (!modules[m]) modules[m] = {};
        if (!modules[m][rkey]) modules[m][rkey] = { addr: item.address_offset, name: item.reg_name, bitfields: [] };
        modules[m][rkey].bitfields.push(item);
    });

    let modIdx = 0;
    for (const [modName, regs] of Object.entries(modules)) {
        const regList = Object.values(regs).sort((a, b) => a.addr.localeCompare(b.addr));
        regList.forEach(r => r.bitfields.sort((a, b) => a.start_bit - b.start_bit));
        const modId = `rmod-${modIdx++}`.replace(/[^a-zA-Z0-9-]/g, '');

        // 模块汇总行
        const totalRegs = regList.length;
        const modTr = document.createElement("tr");
        modTr.className = `rmod-group-${modId}`;
        modTr.innerHTML = `
            <td><span class="debug-toggle" onclick="toggleResGroup('${modId}', 0, this)">+</span> <strong>${escapeHtml(modName)}</strong></td>
            <td colspan="2" style="color:#888;">${totalRegs} 个寄存器</td>
            <td colspan="3"></td>
            ${dumpNames.map(() => '<td></td>').join("")}
            ${dumpNames.length > 1 ? '<td></td>' : ''}
            <td></td>
        `;
        tbody.appendChild(modTr);

        // 每个寄存器
        let regIdx = 0;
        for (const reg of regList) {
            const regId = `${modId}-${regIdx++}`;
            const bf = reg.bitfields[0]; // 第一个 bitfield 作为摘要
            const bitCount = reg.bitfields.length;

            const start = bf.start_bit;
            const end = bf.end_bit;
            const width = end - start + 1;
            const bitRange = start === end ? `[${start}]` : `[${end}:${start}]`;
            const fieldValues = bf.field_values || [];
            const changed = bf.changed;
            const enumMeaning = bf.enum_meaning || "";
            const isUndefined = bf.reg_name === "[未定义寄存器]";
            let isAllZero = false, isAllF = false;
            if (!isUndefined && fieldValues.length > 0 && fieldValues[0] != null) {
                isAllZero = fieldValues.every(v => v === 0);
                isAllF = fieldValues.every(v => v === ((1 << width) - 1));
            }

            // 寄存器摘要行（隐藏，点击模块+才显示）
            const regTr = document.createElement("tr");
            regTr.className = `rmod-group-${modId}`;
            regTr.style.display = "none";
            let regHtml = `
                <td style="padding-left:24px;"><span class="debug-toggle" onclick="toggleResReg('${modId}', '${regId}', this)">+</span> ${escapeHtml(reg.name)}</td>
                <td>${escapeHtml(reg.addr)}</td>
                <td style="color:#888;">${bitCount > 1 ? `${bitCount} 个字段 (最低位)` : ""}</td>
                <td style="text-align:center;">${bitRange}</td>
                <td>${escapeHtml(bf.field_name)}</td>
            `;
            fieldValues.forEach((v, di) => {
                let cls = "";
                if (changed) cls += " debug-cell-changed";
                if (isAllZero) cls += " debug-cell-zero";
                if (isAllF) cls += " debug-cell-allf";
                regHtml += `<td style="text-align:center;" class="${cls}">${v != null ? formatFieldValue(v, width) : "-"}</td>`;
            });
            if (dumpNames.length > 1) {
                regHtml += `<td style="text-align:center;" class="${changed ? 'debug-cell-changed' : ''}">${changed ? '是' : '否'}</td>`;
            }
            regHtml += `<td>${escapeHtml(enumMeaning)}</td>`;
            regTr.innerHTML = regHtml;
            tbody.appendChild(regTr);

            // bitfield 详细行（隐藏，点击寄存器+才显示）
            reg.bitfields.forEach((bf2, bfIdx) => {
                const s2 = bf2.start_bit;
                const e2 = bf2.end_bit;
                const w2 = e2 - s2 + 1;
                const br2 = s2 === e2 ? `[${s2}]` : `[${e2}:${s2}]`;
                const fv2 = bf2.field_values || [];
                const ch2 = bf2.changed;
                const em2 = bf2.enum_meaning || "";
                const ud2 = bf2.reg_name === "[未定义寄存器]";
                let iz2 = false, if2 = false;
                if (!ud2 && fv2.length > 0 && fv2[0] != null) {
                    iz2 = fv2.every(v => v === 0);
                    if2 = fv2.every(v => v === ((1 << w2) - 1));
                }

                const bfTr = document.createElement("tr");
                bfTr.className = `rreg-group-${regId}`;
                bfTr.style.display = "none";
                let bfHtml = `
                    <td style="padding-left:48px;"></td>
                    <td></td>
                    <td></td>
                    <td style="text-align:center;">${br2}</td>
                    <td>${escapeHtml(bf2.field_name)}</td>
                `;
                fv2.forEach((v, di) => {
                    let cls = "";
                    if (ch2) cls += " debug-cell-changed";
                    if (iz2) cls += " debug-cell-zero";
                    if (if2) cls += " debug-cell-allf";
                    bfHtml += `<td style="text-align:center;" class="${cls}">${v != null ? formatFieldValue(v, w2) : "-"}</td>`;
                });
                if (dumpNames.length > 1) {
                    bfHtml += `<td style="text-align:center;" class="${ch2 ? 'debug-cell-changed' : ''}">${ch2 ? '是' : '否'}</td>`;
                }
                bfHtml += `<td>${escapeHtml(em2)}</td>`;
                bfTr.innerHTML = bfHtml;
                tbody.appendChild(bfTr);
            });
        }
    }
}

/**
 * 渲染异常点表格（带期望值列 + 红色高亮）
 */
function renderAbnormalResultTable(theadId, tbodyId, results, dumpNames) {
    const table = findOrCreateTable(theadId.replace("-head",""));
    if (!table) return;
    const thead = table.querySelector("thead");
    const tbody = table.querySelector("tbody");

    thead.innerHTML = "";
    tbody.innerHTML = "";
    const headerRow = document.createElement("tr");
    headerRow.innerHTML = `
        <th>模块</th>
        <th>地址偏移</th>
        <th>寄存器名</th>
        <th>Bit范围</th>
        <th>字段名</th>
        <th>期望值</th>
        ${dumpNames.map(n => `<th>${escapeHtml(n)} 值</th>`).join("")}
        ${dumpNames.length > 1 ? '<th>变化</th>' : ''}
        <th>枚举含义</th>
    `;
    thead.appendChild(headerRow);

    tbody.innerHTML = "";
    results.forEach(item => {
        const tr = document.createElement("tr");
        const start = item.start_bit;
        const end = item.end_bit;
        const width = end - start + 1;
        const bitRange = start === end ? `[${start}]` : `[${end}:${start}]`;
        const fieldValues = item.field_values || [];
        const changed = item.changed;
        const enumMeaning = item.enum_meaning || "";
        const expectedVal = item.expected_value || "";

        let html = `
            <td>${escapeHtml(item.module)}</td>
            <td>${escapeHtml(item.address_offset)}</td>
            <td>${escapeHtml(item.reg_name)}</td>
            <td style="text-align:center;">${bitRange}</td>
            <td>${escapeHtml(item.field_name)}</td>
            <td style="text-align:center;" class="debug-cell-abnormal">${escapeHtml(expectedVal)}</td>
        `;

        fieldValues.forEach((v, di) => {
            html += `<td style="text-align:center;" class="debug-cell-abnormal">${v != null ? formatFieldValue(v, width) : "-"}</td>`;
        });

        if (dumpNames.length > 1) {
            html += `<td style="text-align:center;" class="debug-cell-abnormal">${changed ? '是' : '否'}</td>`;
        }

        html += `<td class="debug-cell-abnormal">${escapeHtml(enumMeaning)}</td>`;
        tr.innerHTML = html;
        tbody.appendChild(tr);
    });
}

// ==================== Excel 导出（三类） ====================

/**
 * 导出完整结果（含未定义寄存器）
 */
async function exportAllExcel() {
    if (!checkResultReady()) return;

    try {
        const resp = await fetch(`${API_BASE}/export_excel`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
        });

        if (!resp.ok) {
            const err = await resp.json();
            alert("导出失败: " + (err.error || "未知错误"));
            return;
        }

        downloadBlob(await resp.blob(), "register_dump_all.xlsx");
    } catch (e) {
        alert("导出失败: " + e.message);
    }
}

/**
 * 导出已定义寄存器（排除[未定义寄存器]）并筛选表格
 */
async function exportDefinedExcel() {
    if (!checkResultReady()) return;

    try {
        const resp = await fetch(`${API_BASE}/export_defined_excel`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
        });

        if (!resp.ok) {
            const err = await resp.json();
            alert("导出失败: " + (err.error || "未知错误"));
            return;
        }

        downloadBlob(await resp.blob(), "register_dump_defined.xlsx");
    } catch (e) {
        alert("导出失败: " + e.message);
    }
}

/**
 * 导出疑似异常点（不符合正常值的寄存器）并筛选表格
 */
async function exportAbnormalExcel() {
    if (!checkResultReady()) return;

    try {
        const resp = await fetch(`${API_BASE}/export_abnormal_excel`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
        });

        if (!resp.ok) {
            const err = await resp.json();
            alert("导出失败: " + (err.error || "未知错误"));
            return;
        }

        downloadBlob(await resp.blob(), "abnormal_registers.xlsx");
    } catch (e) {
        alert("导出失败: " + e.message);
    }
}

function checkResultReady() {
    if ($("#result-section").style.display === "none") {
        alert("没有可导出的数据，请先解析 dump 文件！");
        return false;
    }
    return true;
}

function downloadBlob(blob, filename) {
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
}

// ==================== JSON 示例 ====================

const JSON_EXAMPLE = {
    "module": "模块名称（与dump文件中:::前的模块名一致）",
    "registers": [
        {
            "name": "寄存器名称（助记名）",
            "address_offset": "0x00000000",
            "bitfields": [
                {
                    "start_bit": 0,
                    "end_bit": 0,
                    "field_name": "单bit字段示例（仅正常值）",
                    "expected_value": "1"
                },
                {
                    "start_bit": 1,
                    "end_bit": 1,
                    "field_name": "单bit字段示例（仅异常值）",
                    "abnormal_value": "0"
                },
                {
                    "start_bit": 2,
                    "end_bit": 3,
                    "field_name": "多bit字段示例（正常值+异常值）",
                    "expected_value": "0x3",
                    "abnormal_value": "0x0",
                    "enums": [
                        {"value": "0x0", "meaning": "IDLE"},
                        {"value": "0x1", "meaning": "RUN"},
                        {"value": "0x2", "meaning": "DONE"},
                        {"value": "0x3", "meaning": "ERROR"}
                    ]
                },
                {
                    "start_bit": 4,
                    "end_bit": 7,
                    "field_name": "无枚举无值的字段"
                }
            ]
        }
    ]
};

function showJsonExample() {
    document.getElementById("json-example-content").textContent =
        JSON.stringify(JSON_EXAMPLE, null, 2);
    document.getElementById("modal-json-example").style.display = "flex";
}

function closeJsonExample() {
    document.getElementById("modal-json-example").style.display = "none";
}

async function copyJsonExample() {
    const text = document.getElementById("json-example-content").textContent;
    try {
        await navigator.clipboard.writeText(text);
        alert("已复制到剪贴板！");
    } catch (e) {
        // fallback
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        alert("已复制到剪贴板！");
    }
}

// ==================== HTML 转义 ====================
function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

function escapeAttr(str) {
    return str.replace(/'/g, "\\'").replace(/"/g, "&quot;");
}
