(function () {
    "use strict";

    const state = {
        medicines: [],
        filtered: [],
        view: "grid",
        category: "All",
        maxPrice: 1000,
        rxOnly: false,
        page: 1,
        pageSize: 12
    };

    const gradients = ["gradient-a", "gradient-b", "gradient-c", "gradient-d", "gradient-e", "gradient-f"];

    document.addEventListener("DOMContentLoaded", function () {
        setupNavigation();
        setupGlobalSearch();
        setupFaq();
        setupHomeMedicines();
        setupSearchPage();
        setupMedicineDetail();
        setupScanner();
        setupDiseasePage();
        setupAssistant();
    });

    function setupNavigation() {
        const path = window.location.pathname;
        document.querySelectorAll("[data-nav]").forEach(function (link) {
            const target = link.getAttribute("data-nav");
            if (target === "/" ? path === "/" : path.startsWith(target || "")) {
                link.classList.add("active");
            }
        });

        const toggle = document.querySelector("[data-nav-toggle]");
        const nav = document.getElementById("mainNav");
        if (!toggle || !nav) return;

        toggle.addEventListener("click", function () {
            const open = nav.classList.toggle("open");
            toggle.setAttribute("aria-expanded", String(open));
        });
    }

    function setupGlobalSearch() {
        document.querySelectorAll("[data-global-search]").forEach(function (form) {
            form.addEventListener("submit", function (event) {
                event.preventDefault();
                const input = form.querySelector("input[name='q']");
                const query = input ? input.value.trim() : "";
                window.location.href = query ? "/search?q=" + encodeURIComponent(query) : "/search";
            });
        });
    }

    function setupFaq() {
        const faq = document.querySelector("[data-faq]");
        if (!faq) return;

        faq.addEventListener("click", function (event) {
            const button = event.target.closest("button");
            if (!button) return;

            const item = button.closest("article");
            const wasOpen = item.classList.contains("open");
            faq.querySelectorAll("article").forEach(function (article) {
                article.classList.remove("open");
                const icon = article.querySelector(".icon");
                if (icon) {
                    icon.classList.remove("icon-minus");
                    icon.classList.add("icon-plus");
                }
            });
            if (!wasOpen) {
                item.classList.add("open");
                const icon = item.querySelector(".icon");
                if (icon) {
                    icon.classList.remove("icon-plus");
                    icon.classList.add("icon-minus");
                }
            }
        });
    }

    async function setupHomeMedicines() {
        const container = document.querySelector("[data-home-medicines]");
        if (!container) return;

        container.innerHTML = skeletonCards(4);
        const data = await fetchJson("/api/cheapest");
        if (!data || !data.success) {
            container.innerHTML = emptyState("Medicine data is unavailable", "Please try again in a moment.");
            return;
        }
        container.innerHTML = data.data.slice(0, 4).map(renderMedicineCard).join("");
    }

    function setupSearchPage() {
        const form = document.getElementById("medicineSearchForm");
        if (!form) return;

        const input = document.getElementById("medicineSearchInput");
        const params = new URLSearchParams(window.location.search);
        const initialQuery = params.get("q") || "";
        if (input) input.value = initialQuery;

        form.addEventListener("submit", function (event) {
            event.preventDefault();
            runSearch(input.value.trim());
        });

        input.addEventListener("input", debounce(function () {
            const query = input.value.trim();
            if (query.length >= 2) runSearch(query);
        }, 280));

        document.querySelectorAll("[data-view]").forEach(function (button) {
            button.addEventListener("click", function () {
                state.view = button.getAttribute("data-view") || "grid";
                document.querySelectorAll("[data-view]").forEach((b) => b.classList.remove("active"));
                button.classList.add("active");
                renderSearchResults();
            });
        });

        document.getElementById("categoryFilters").addEventListener("click", function (event) {
            const button = event.target.closest("button");
            if (!button) return;
            state.category = button.dataset.category || "All";
            this.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
            button.classList.add("active");
            filterResults();
        });

        const maxPrice = document.getElementById("maxPrice");
        const maxPriceLabel = document.getElementById("maxPriceLabel");
        maxPrice.addEventListener("input", function () {
            state.maxPrice = Number(maxPrice.value);
            maxPriceLabel.textContent = "Rs. " + state.maxPrice;
            filterResults();
        });

        document.getElementById("rxOnly").addEventListener("change", function (event) {
            state.rxOnly = event.target.checked;
            filterResults();
        });

        document.getElementById("pagination").addEventListener("click", function (event) {
            const button = event.target.closest("button");
            if (!button) return;
            if (button.hasAttribute("data-page-next")) {
                state.page += 1;
            } else {
                state.page = Number(button.dataset.page || 1);
            }
            renderSearchResults();
        });

        if (initialQuery) {
            runSearch(initialQuery);
        } else {
            loadCheapestForSearch();
        }
    }

    async function loadCheapestForSearch() {
        const container = document.getElementById("searchResults");
        container.innerHTML = skeletonCards(6);
        const data = await fetchJson("/api/cheapest");
        if (!data || !data.success) {
            container.innerHTML = emptyState("Search is ready", "Enter a medicine name, brand, or composition.");
            return;
        }
        state.medicines = data.data;
        filterResults();
    }

    async function runSearch(query) {
        const container = document.getElementById("searchResults");
        const count = document.getElementById("resultCount");
        if (!query) {
            loadCheapestForSearch();
            return;
        }

        container.innerHTML = skeletonCards(6);
        count.textContent = "Searching...";
        const data = await fetchJson("/api/search?q=" + encodeURIComponent(query));
        if (!data || !data.success) {
            container.innerHTML = emptyState("No results found", "Try a medicine name, brand, or composition.");
            count.textContent = "0 results";
            return;
        }
        state.medicines = data.data;
        state.page = 1;
        filterResults();
    }

    function filterResults() {
        state.filtered = state.medicines.filter(function (item) {
            const category = (item.medicine_category || "").toLowerCase();
            const price = Number(item.price || 0);
            const rx = String(item.prescription_required || "").toLowerCase();
            const categoryOk = state.category === "All" || category.includes(state.category.toLowerCase());
            const priceOk = !price || price <= state.maxPrice;
            const rxOk = !state.rxOnly || rx === "true" || rx === "1" || rx.includes("required");
            return categoryOk && priceOk && rxOk;
        });
        state.page = 1;
        renderSearchResults();
    }

    function renderSearchResults() {
        const container = document.getElementById("searchResults");
        const count = document.getElementById("resultCount");
        const pagination = document.getElementById("pagination");
        if (!container) return;

        count.textContent = state.filtered.length + " results";
        container.classList.toggle("list-mode", state.view === "list");
        container.dataset.viewMode = state.view;

        if (!state.filtered.length) {
            container.innerHTML = emptyState("No medicines match these filters", "Try a broader search or increase the maximum price.");
            pagination.hidden = true;
            return;
        }

        const start = (state.page - 1) * state.pageSize;
        const pageItems = state.filtered.slice(start, start + state.pageSize);
        container.innerHTML = pageItems.map(function (item, index) {
            return state.view === "list" ? renderMedicineRow(item, index) : renderMedicineCard(item, index);
        }).join("");

        pagination.hidden = state.filtered.length <= state.pageSize;
        pagination.querySelectorAll("[data-page]").forEach(function (button) {
            button.classList.toggle("active", Number(button.dataset.page) === state.page);
        });
    }

    function setupMedicineDetail() {
        const page = document.querySelector("[data-medicine-id]");
        if (!page) return;

        const id = page.dataset.medicineId;
        const container = document.getElementById("medicineDetail");
        fetchJson("/api/medicine/" + encodeURIComponent(id)).then(function (data) {
            if (!data || !data.success) {
                container.innerHTML = emptyState("Medicine not found", "The existing details API returned no matching medicine.");
                return;
            }
            container.innerHTML = renderMedicineDetail(data.data);
        });
    }

    function setupScanner() {
        const uploadZone = document.getElementById("uploadZone");
        const input = document.getElementById("prescriptionFile");
        if (!uploadZone || !input) return;

        const dropCard = uploadZone.querySelector(".drop-card");
        const preview = document.getElementById("scanPreview");
        const previewFrame = document.getElementById("previewFrame");
        const detectedPanel = document.getElementById("detectedPanel");
        const detectedList = document.getElementById("detectedList");
        const reset = document.querySelector("[data-scan-reset]");

        ["dragover", "dragleave", "drop"].forEach(function (name) {
            uploadZone.addEventListener(name, function (event) {
                event.preventDefault();
                dropCard.classList.toggle("dragging", name === "dragover");
                if (name === "drop" && event.dataTransfer.files[0]) {
                    startScan(event.dataTransfer.files[0]);
                }
            });
        });

        input.addEventListener("change", function () {
            if (input.files[0]) startScan(input.files[0]);
        });

        reset.addEventListener("click", function () {
            input.value = "";
            dropCard.hidden = false;
            preview.hidden = true;
            detectedPanel.hidden = true;
            reset.hidden = true;
            previewFrame.innerHTML = '<span class="icon icon-file"></span>';
        });

        async function startScan(file) {
            dropCard.hidden = true;
            preview.hidden = false;
            detectedPanel.hidden = true;
            reset.hidden = true;
            previewFrame.classList.add("scanning");
            setText("scanStateLabel", "Scanning...");
            setText("scanTitle", "Reading your prescription");
            setText("scanCopy", "Calling the existing OCR backend.");

            if (file.type.startsWith("image/")) {
                const url = URL.createObjectURL(file);
                previewFrame.innerHTML = '<img src="' + url + '" alt="Prescription preview">';
            }

            const formData = new FormData();
            formData.append("file", file);
            const response = await fetchJson("/api/ocr", { method: "POST", body: formData });

            previewFrame.classList.remove("scanning");
            reset.hidden = false;
            if (!response || response.success === false) {
                setText("scanStateLabel", "OCR unavailable");
                setText("scanTitle", "OCR service is not available");
                setText("scanCopy", "The prescription upload was received, but automated extraction is not active in this build.");
                return;
            }

            setText("scanStateLabel", "Scan complete");
            setText("scanTitle", "Medicines detected");
            setText("scanCopy", "Review the detected items below.");
            const items = response.data || response.medicines || [];
            detectedList.innerHTML = items.map(renderDetectedMedicine).join("") || emptyState("No medicines detected", "Try a clearer image.");
            detectedPanel.hidden = false;
        }
    }

    function setupDiseasePage() {
        const list = document.getElementById("diseaseList");
        const detail = document.getElementById("diseaseDetail");
        const input = document.getElementById("diseaseSearchInput");
        if (!list || !detail) return;

        const diseases = [
            {
                id: "diabetes",
                name: "Type 2 Diabetes",
                category: "Endocrine",
                severity: "Chronic",
                emergency: "Seek immediate care if blood sugar is very high or symptoms of ketoacidosis appear.",
                symptoms: ["Frequent urination", "Increased thirst", "Fatigue", "Blurred vision"],
                causes: ["Insulin resistance", "Genetics", "Obesity", "Sedentary lifestyle"],
                lifestyle: ["30 min daily exercise", "Weight management", "Stress control", "Regular checkups"],
                diet: ["Whole grains", "Leafy vegetables", "Lean protein", "Avoid sugary drinks"]
            },
            {
                id: "hypertension",
                name: "Hypertension",
                category: "Cardiovascular",
                severity: "Chronic",
                emergency: "Blood pressure above 180/120 with symptoms needs emergency care.",
                symptoms: ["Headache", "Dizziness", "Chest tightness", "Often asymptomatic"],
                causes: ["High sodium diet", "Stress", "Obesity", "Genetics"],
                lifestyle: ["Reduce salt", "Regular exercise", "Sleep 7 to 8 hours", "Monitor BP weekly"],
                diet: ["DASH diet", "Potassium-rich foods", "Reduce processed foods"]
            },
            {
                id: "asthma",
                name: "Asthma",
                category: "Respiratory",
                severity: "Chronic",
                emergency: "Blue lips or inability to speak requires urgent care.",
                symptoms: ["Wheezing", "Shortness of breath", "Chest tightness", "Coughing at night"],
                causes: ["Allergens", "Air pollution", "Genetics"],
                lifestyle: ["Avoid triggers", "Use spacer with inhaler", "Vaccinate against flu"],
                diet: ["Vitamin D-rich foods", "Omega-3", "Avoid sulfite foods"]
            }
        ];

        let active = diseases[0].id;
        renderDiseaseList(diseases);
        renderDiseaseDetail(diseases[0]);

        input.addEventListener("input", function () {
            const query = input.value.trim().toLowerCase();
            renderDiseaseList(diseases.filter((d) => d.name.toLowerCase().includes(query)));
        });

        list.addEventListener("click", async function (event) {
            const button = event.target.closest("button");
            if (!button) return;
            active = button.dataset.id;
            list.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
            button.classList.add("active");

            const apiData = await fetchJson("/api/disease?q=" + encodeURIComponent(button.dataset.name || ""));
            if (apiData && apiData.success && apiData.data) {
                renderDiseaseDetail(apiData.data);
                return;
            }
            renderDiseaseDetail(diseases.find((d) => d.id === active) || diseases[0], true);
        });

        function renderDiseaseList(items) {
            list.innerHTML = items.map(function (d) {
                return '<button type="button" class="' + (d.id === active ? "active" : "") + '" data-id="' + d.id + '" data-name="' + escapeHtml(d.name) + '">' +
                    '<strong>' + escapeHtml(d.name) + '</strong><small>' + escapeHtml(d.category) + ' | ' + escapeHtml(d.severity) + '</small></button>';
            }).join("") || '<p class="empty-copy">No matching conditions.</p>';
        }

        function renderDiseaseDetail(d, fallback) {
            const note = fallback ? '<p class="subline">Showing general guidance for this condition.</p>' : "";
            detail.innerHTML =
                '<article class="disease-hero">' +
                '<div class="badge-row"><span class="badge">' + escapeHtml(d.category || "Guidance") + '</span><span class="badge secondary">' + escapeHtml(d.severity || "Info") + '</span></div>' +
                '<h2>' + escapeHtml(d.name || "Disease Guidance") + '</h2>' + note +
                '<div class="alert-box"><span class="icon icon-shield"></span><div><strong>Emergency</strong><p>' + escapeHtml(d.emergency || "Consult a doctor for urgent or worsening symptoms.") + '</p></div></div>' +
                '</article>' +
                '<div class="disease-panels">' +
                panel("Symptoms", "activity", d.symptoms) +
                panel("Causes", "heart", d.causes) +
                panel("Lifestyle", "stethoscope", d.lifestyle) +
                panel("Diet", "plus", d.diet) +
                '</div>';
        }
    }

    function setupAssistant() {
        const form = document.getElementById("assistantForm");
        const input = document.getElementById("assistantInput");
        const messages = document.getElementById("chatMessages");
        const suggestions = document.getElementById("chatSuggestions");
        if (!form || !input || !messages) return;

        form.addEventListener("submit", function (event) {
            event.preventDefault();
            send(input.value);
        });

        suggestions.addEventListener("click", function (event) {
            const button = event.target.closest("button");
            if (button) send(button.textContent);
        });

        document.querySelector("[data-new-chat]").addEventListener("click", function () {
            messages.innerHTML = '<div class="chat-row ai"><span class="avatar-icon icon icon-bot"></span><div class="bubble">Hi. I am your MED FINDER assistant. Ask about medicines, doses, alternatives, or when to see a doctor.</div></div>';
            suggestions.hidden = false;
        });

        async function send(text) {
            const value = (text || "").trim();
            if (!value) return;
            suggestions.hidden = true;
            messages.insertAdjacentHTML("beforeend", chatBubble("user", value));
            input.value = "";
            messages.insertAdjacentHTML("beforeend", '<div class="chat-row ai typing-row"><span class="avatar-icon icon icon-bot"></span><div class="bubble">Thinking...</div></div>');
            messages.scrollTop = messages.scrollHeight;

            const response = await fetchJson("/api/assistant", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: value })
            });
            const typing = messages.querySelector(".typing-row");
            if (typing) typing.remove();
            const reply = response && response.success
                ? (response.reply || response.data || "I found a response from the assistant service.")
                : "The AI assistant is not available in this build yet. Please use medicine search for database-backed results.";
            messages.insertAdjacentHTML("beforeend", chatBubble("ai", String(reply)));
            messages.scrollTop = messages.scrollHeight;
        }
    }

    function renderMedicineCard(item, index) {
        const m = normalizeMedicine(item);
        const gradient = gradients[(Number(m.id) + (index || 0)) % gradients.length];
        return '<a class="medicine-card card-hover" href="/medicine/' + encodeURIComponent(m.id) + '">' +
            '<div class="medicine-visual ' + gradient + '">' +
            (m.rx ? '<span class="rx-badge">Rx</span>' : '') +
            '<span class="form-badge">' + escapeHtml(m.form || "Med") + '</span>' +
            '<span class="icon icon-pill"></span></div>' +
            '<div class="medicine-body">' +
            '<small>' + escapeHtml(m.brand) + '</small>' +
            '<h3>' + escapeHtml(m.name) + '</h3>' +
            '<small>' + escapeHtml(m.manufacturer || m.category || "MED FINDER") + '</small>' +
            '<div class="medicine-meta"><span class="stars">Rating</span><strong>4.' + ((Number(m.id) % 5) + 3) + '</strong><span>| ' + escapeHtml(m.category || "Medicine") + '</span></div>' +
            '<div class="price-row"><div><div class="price">Rs. ' + escapeHtml(formatPrice(m.price)) + '</div><div class="mrp">Rs. ' + escapeHtml(formatPrice(m.mrp)) + '</div></div><span class="view-pill">View</span></div>' +
            '</div></a>';
    }

    function renderMedicineRow(item, index) {
        const m = normalizeMedicine(item);
        const gradient = gradients[(Number(m.id) + (index || 0)) % gradients.length];
        return '<a class="result-row card-hover" href="/medicine/' + encodeURIComponent(m.id) + '">' +
            '<div class="medicine-visual ' + gradient + '"><span class="icon icon-pill"></span></div>' +
            '<div><h3>' + escapeHtml(m.name) + '</h3><p>' + escapeHtml(m.brand) + ' | ' + escapeHtml(m.composition || m.category || "Medicine") + '</p></div>' +
            '<div class="price-row"><div><div class="price">Rs. ' + escapeHtml(formatPrice(m.price)) + '</div><div class="mrp">Rs. ' + escapeHtml(formatPrice(m.mrp)) + '</div></div></div>' +
            '</a>';
    }

    function renderMedicineDetail(item) {
        const m = normalizeMedicine(item);
        const gradient = gradients[Number(m.id) % gradients.length];
        return '<section class="panel glass detail-visual">' +
            '<div class="medicine-art ' + gradient + '"><span class="icon icon-pill"></span>' + (m.rx ? '<span class="rx-badge">Prescription</span>' : '') + '</div>' +
            '<div class="detail-price-panel"><div class="price">Rs. ' + escapeHtml(formatPrice(m.price)) + '</div><div class="mrp">MRP Rs. ' + escapeHtml(formatPrice(m.mrp)) + '</div></div>' +
            '<button class="btn btn-gradient btn-block" type="button">Buy now</button><button class="btn btn-outline btn-block" type="button">Save to list</button>' +
            '</section>' +
            '<section><article class="panel surface-only detail-copy">' +
            '<div class="badge-row"><span class="badge">' + escapeHtml(m.category || "Medicine") + '</span><span class="badge secondary">' + escapeHtml(m.form || "Form") + '</span><span class="badge">4.' + ((Number(m.id) % 5) + 3) + ' rating</span></div>' +
            '<h1>' + escapeHtml(m.name) + '</h1><p class="subline">' + escapeHtml(m.brand || "Brand") + ' | by ' + escapeHtml(m.manufacturer || "MED FINDER") + '</p>' +
            '<dl class="field-grid"><div><dt>Strength</dt><dd>' + escapeHtml(m.strength || "-") + '</dd></div><div><dt>Form</dt><dd>' + escapeHtml(m.form || "-") + '</dd></div><div><dt>Composition</dt><dd>' + escapeHtml(m.composition || "-") + '</dd></div></dl>' +
            '</article>' +
            '<article class="ai-panel"><h2><span class="icon icon-sparkles"></span> AI Recommendation</h2><p>Review composition, strength, and prescription status carefully. Confirm alternatives with a doctor or pharmacist before switching medicines.</p></article>' +
            '<div class="detail-info-grid">' +
            infoCard("Uses", "plus", [m.category || "Medicine information", "Verify dosage with a clinician"]) +
            infoCard("Benefits", "sparkles", ["Compare price and composition", "Find related medicines from search"]) +
            infoCard("Side Effects", "shield", ["Check the product label", "Report unusual reactions"]) +
            infoCard("Warnings", "heart", [m.rx ? "Prescription required" : "Follow label directions", "Consult a doctor before starting"]) +
            '</div></section>';
    }

    function renderDetectedMedicine(item) {
        const name = item.name || item.medicine_name || "Detected medicine";
        const dose = item.dose || item.instructions || "Review dosage before use";
        const confidence = Number(item.confidence || 0);
        return '<div><span class="feature-icon soft"><span class="icon icon-plus"></span></span><div class="min-grow"><strong>' + escapeHtml(name) + '</strong><small>' + escapeHtml(dose) + '</small></div>' +
            '<div class="confidence"><div class="confidence-label"><span>Confidence</span><strong>' + confidence + '%</strong></div><div class="confidence-track"><span style="width:' + Math.max(0, Math.min(100, confidence)) + '%"></span></div></div></div>';
    }

    function normalizeMedicine(item) {
        const price = Number(item.price || item.mrp || 0);
        return {
            id: item.id || item.medicine_id || "",
            name: item.medicine_name || item.name || "Unnamed medicine",
            brand: item.brand_name || item.brand || "Unknown brand",
            manufacturer: item.manufacturer || item.company || item.manufacturer_name || "",
            strength: item.strength || item.dosage_strength || "",
            form: item.dosage_form || item.form || item.packaging || "Medicine",
            composition: item.composition || item.generic_name || "",
            price: price,
            mrp: Number(item.mrp || (price ? Math.round(price * 1.18) : 0)),
            category: item.medicine_category || item.category || "",
            rx: String(item.prescription_required || item.rx || "").toLowerCase() === "true" || String(item.prescription_required || "").includes("Required")
        };
    }

    function infoCard(title, icon, items) {
        return '<article class="info-panel"><h3><span class="icon icon-' + icon + '"></span>' + escapeHtml(title) + '</h3><ul>' +
            items.map((item) => '<li>' + escapeHtml(item) + '</li>').join("") + '</ul></article>';
    }

    function panel(title, icon, items) {
        return infoCard(title, icon, Array.isArray(items) ? items : []);
    }

    function chatBubble(role, text) {
        const icon = role === "user" ? "icon-message" : "icon-bot";
        return '<div class="chat-row ' + role + '">' +
            (role === "ai" ? '<span class="avatar-icon icon ' + icon + '"></span>' : '') +
            '<div class="bubble">' + escapeHtml(text) + '</div>' +
            (role === "user" ? '<span class="avatar-icon icon ' + icon + '"></span>' : '') +
            '</div>';
    }

    async function fetchJson(url, options) {
        try {
            const response = await fetch(url, options);
            if (!response.ok) return null;
            return await response.json();
        } catch (error) {
            return null;
        }
    }

    function skeletonCards(count) {
        return Array.from({ length: count }, function () {
            return '<div class="medicine-card"><div class="medicine-visual skeleton-block"></div><div class="medicine-body"><div class="skeleton-block short"></div><div class="skeleton-block"></div><div class="skeleton-block short"></div></div></div>';
        }).join("");
    }

    function emptyState(title, copy) {
        return '<div class="empty-state panel surface-only"><span class="feature-icon"><span class="icon icon-search"></span></span><h2>' + escapeHtml(title) + '</h2><p>' + escapeHtml(copy) + '</p></div>';
    }

    function setText(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    }

    function formatPrice(value) {
        const number = Number(value || 0);
        if (!number) return "-";
        return number.toFixed(number % 1 ? 2 : 0);
    }

    function escapeHtml(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function debounce(fn, wait) {
        let timeout;
        return function () {
            const args = arguments;
            clearTimeout(timeout);
            timeout = setTimeout(function () {
                fn.apply(null, args);
            }, wait);
        };
    }
})();
