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
        pageSize: 12,
        searchRequestId: 0
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
                link.setAttribute("aria-current", "page");
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
            const input = form.querySelector("input[name='q']");
            const box = form.querySelector(".search-suggest");
            let items = [];
            let activeIndex = -1;
            let requestId = 0;

            if (!input || !box) return;

            function hideSuggestions() {
                items = [];
                activeIndex = -1;
                requestId += 1;
                box.hidden = true;
                box.innerHTML = "";
                input.setAttribute("aria-expanded", "false");
                input.removeAttribute("aria-activedescendant");
            }

            function openSuggestion(item) {
                loadSuggestionsSoon.cancel();
                if (form.hasAttribute("data-suggest-inline")) {
                    input.value = item.name;
                    hideSuggestions();
                    form.dispatchEvent(new CustomEvent("medfinder:search", {
                        detail: { query: item.name },
                    }));
                    return;
                }
                window.location.href = "/search?q=" + encodeURIComponent(item.name);
            }

            function setActive(index) {
                const buttons = box.querySelectorAll("button[role='option']");
                activeIndex = index;
                buttons.forEach(function (button, position) {
                    button.setAttribute("aria-selected", String(position === index));
                });
                if (buttons[index]) {
                    input.setAttribute("aria-activedescendant", buttons[index].id);
                    buttons[index].scrollIntoView({ block: "nearest" });
                } else {
                    input.removeAttribute("aria-activedescendant");
                }
            }

            function renderSuggestions(list) {
                items = list;
                if (!list.length) {
                    hideSuggestions();
                    return;
                }
                box.innerHTML = list.map(function (item, index) {
                    const meta = [item.form, item.strength, item.pack].filter(Boolean).join(" · ");
                    const price = item.price
                        ? "Rs. " + formatPrice(item.price)
                            + (item.price_per_unit && item.unit
                                ? " (Rs. " + formatUnitPrice(item.price_per_unit) + "/" + escapeHtml(item.unit) + ")"
                                : "")
                        : "";
                    return '<button type="button" role="option" id="heroSuggestion' + index + '"'
                        + ' aria-selected="false" data-index="' + index + '">'
                        + '<span class="suggest-name">' + escapeHtml(item.name) + "</span>"
                        + '<span class="suggest-price">' + price + "</span>"
                        + '<span class="suggest-meta">' + escapeHtml(meta)
                        + (item.price_label ? " · " + escapeHtml(item.price_label) : "") + "</span>"
                        + "</button>";
                }).join("");
                box.hidden = false;
                input.setAttribute("aria-expanded", "true");
                activeIndex = -1;
            }

            function submitSearch() {
                if (activeIndex >= 0 && items[activeIndex]) {
                    openSuggestion(items[activeIndex]);
                    return;
                }
                loadSuggestionsSoon.cancel();
                const query = input.value.trim();
                if (form.hasAttribute("data-suggest-inline")) {
                    hideSuggestions();
                    if (query) {
                        form.dispatchEvent(new CustomEvent("medfinder:search", {
                            detail: { query: query },
                        }));
                    }
                    return;
                }
                window.location.href = query ? "/search?q=" + encodeURIComponent(query) : "/search";
            }

            form.addEventListener("submit", function (event) {
                event.preventDefault();
                submitSearch();
            });

            // Keep focus in the input so the click lands before any blur handler.
            box.addEventListener("mousedown", function (event) {
                event.preventDefault();
            });
            box.addEventListener("click", function (event) {
                const button = event.target.closest("button[data-index]");
                if (!button) return;
                const item = items[Number(button.dataset.index)];
                if (item) openSuggestion(item);
            });

            function loadSuggestions(query) {
                const current = ++requestId;
                fetchJson("/api/search-suggestions?q=" + encodeURIComponent(query)).then(function (data) {
                    if (current !== requestId || !input.matches(":focus")) return;
                    if (!data || !data.success) {
                        hideSuggestions();
                        return;
                    }
                    renderSuggestions(data.data || []);
                });
            }

            // Shows recommendations before anything is typed, then narrows them
            // to the typed prefix (a single letter is enough).
            input.addEventListener("focus", function () {
                if (!items.length) loadSuggestions(input.value.trim());
            });

            const loadSuggestionsSoon = debounce(function () {
                loadSuggestions(input.value.trim());
            }, 220);

            input.addEventListener("input", loadSuggestionsSoon);

            input.addEventListener("keydown", function (event) {
                if (event.key === "Escape") {
                    if (items.length) {
                        event.preventDefault();
                        hideSuggestions();
                    }
                    return;
                }
                if (event.key === "Enter") {
                    event.preventDefault();
                    submitSearch();
                    return;
                }
                if (!items.length) return;
                if (event.key === "ArrowDown") {
                    event.preventDefault();
                    setActive(activeIndex >= items.length - 1 ? 0 : activeIndex + 1);
                } else if (event.key === "ArrowUp") {
                    event.preventDefault();
                    setActive(activeIndex <= 0 ? items.length - 1 : activeIndex - 1);
                }
            });

            document.addEventListener("click", function (event) {
                if (!box.hidden && !form.contains(event.target)) hideSuggestions();
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
                const trigger = article.querySelector("button");
                if (trigger) trigger.setAttribute("aria-expanded", "false");
            });
            if (!wasOpen) {
                item.classList.add("open");
                const icon = item.querySelector(".icon");
                if (icon) {
                    icon.classList.remove("icon-plus");
                    icon.classList.add("icon-minus");
                }
                button.setAttribute("aria-expanded", "true");
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

        form.addEventListener("medfinder:search", function (event) {
            const query = event.detail && event.detail.query ? event.detail.query : "";
            runSearchSoon.cancel();
            runSearch(query.trim());
        });

        const runSearchSoon = debounce(function () {
            const query = input.value.trim();
            if (query.length >= 2) runSearch(query);
        }, 280);
        input.addEventListener("input", runSearchSoon);

        document.querySelectorAll("[data-view]").forEach(function (button) {
            button.addEventListener("click", function () {
                state.view = button.getAttribute("data-view") || "grid";
                document.querySelectorAll("[data-view]").forEach((b) => {
                    b.classList.remove("active");
                    b.setAttribute("aria-pressed", "false");
                });
                button.classList.add("active");
                button.setAttribute("aria-pressed", "true");
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
        clearCheaperAlternatives();
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
        count.textContent = "Searching…";
        const requestId = ++state.searchRequestId;
        const data = await fetchJson("/api/search?q=" + encodeURIComponent(query));
        // Ignore older responses when a user types a more specific query quickly.
        if (requestId !== state.searchRequestId) return;
        if (!data || !data.success) {
            container.innerHTML = emptyState("No results found", "Try a medicine name, brand, or composition.");
            count.textContent = "0 results";
            return;
        }
        state.medicines = data.data;
        state.page = 1;
        filterResults();
        loadCheaperAlternatives(data.data[0], requestId);
    }

    function clearCheaperAlternatives() {
        const panel = document.getElementById("cheaperAlternatives");
        if (!panel) return;
        panel.hidden = true;
        panel.innerHTML = "";
    }

    async function loadCheaperAlternatives(medicine, requestId) {
        const panel = document.getElementById("cheaperAlternatives");
        if (!panel) return;
        clearCheaperAlternatives();
        if (!medicine || !medicine.id) return;

        // The server may fetch current pharmacy prices first, which takes a few seconds.
        panel.innerHTML = '<p class="cheaper-loading" role="status">Checking current prices for cheaper same-composition options…</p>';
        panel.hidden = false;
        const data = await fetchJson("/api/substitutes/" + encodeURIComponent(medicine.id) + "?limit=6");
        if (requestId !== state.searchRequestId) return;
        clearCheaperAlternatives();
        if (!data || !data.success) return;

        const cheaper = (data.same_composition_alternatives || []).filter(function (item) {
            return Number(item.savings_percent || 0) > 0 && Number(item.price || 0) > 0;
        });
        const options = (data.same_composition_options || []).filter(function (item) {
            return Number(item.price || 0) > 0;
        });
        const referenceOptions = (data.reference_price_options || []).filter(function (item) {
            return Number(item.price || 0) > 0;
        });
        if (!cheaper.length && !options.length && !referenceOptions.length) return;

        const priceKnown = cheaper.length > 0;
        const estimated = !priceKnown && options.some(function (item) {
            return Number(item.estimated_savings_percent || 0) > 0;
        });
        const referencePriceMissing = !data.reference_price_available;
        const referencePriceCurrent = Boolean(data.reference_price_is_current);
        const eyebrow = priceKnown
            ? 'Save on the same content'
            : estimated
                ? 'Estimated saving (catalog prices)'
                : referencePriceMissing
                    ? 'No recorded price for this medicine'
                    : referencePriceCurrent ? 'No cheaper current listing found' : 'Current price not confirmed';
        const blurb = priceKnown
            ? 'Same recorded ingredients and strength, with current listing prices on both sides. These are catalog matches, not verified as therapeutically interchangeable.'
            : estimated
                ? 'These products have the same recorded ingredients, strength and form. Estimated savings compare recorded catalog prices per unit; those prices are not confirmed as current, so check the price at your pharmacy. Not verified as interchangeable - confirm with a doctor or pharmacist.'
            : referencePriceMissing
                ? 'This medicine has no recorded price, so these are the cheapest catalog products with the same recorded ingredients, strength and form. Their prices are unverified; confirm with a pharmacist.'
                : referencePriceCurrent
                    ? 'This price is a current listing, but no same-composition product with a current listing price is cheaper. The options below carry old, unverified catalog prices, so no saving is claimed.'
                    : 'The price recorded for this medicine is an old catalog price, not a confirmed current listing, so no saving is claimed. These products share the recorded ingredients, strength and form; their catalog prices are unverified and they are not verified interchangeable.';
        const items = priceKnown ? cheaper : options;
        const catalogHtml = !items.length ? '' : '<div class="cheaper-head">' +
            '<div><p class="eyebrow">' + eyebrow + '</p>' +
            '<h2>' + (priceKnown ? 'Cheaper options for ' : 'Same-composition catalog options for ') +
            escapeHtml(medicine.medicine_name || medicine.name || 'this medicine') + '</h2>' +
            '<p>' + blurb + '</p></div>' +
            '<span class="count-badge">' + items.length + '</span>' +
            '</div>' +
            '<div class="cheaper-list">' + items.map(function (item) {
                const unitLabel = item.price_per_unit_value
                    ? 'Rs. ' + formatUnitPrice(item.price_per_unit_value) + ' per unit'
                    : (item.savings_basis === 'per pack' ? 'Same pack size' : '');
                return '<a class="cheaper-item" href="/medicine/' + encodeURIComponent(item.id) + '">' +
                    '<div class="cheaper-copy"><strong>' + escapeHtml(item.medicine_name) + '</strong>' +
                    '<small>' + escapeHtml(item.brand_name || item.manufacturer || 'Same composition') +
                    ' · ' + escapeHtml(item.pack_size || 'Pack unknown') + '</small></div>' +
                    '<div class="cheaper-price"><span>Rs. ' + formatPrice(item.price) + '</span>' +
                    (priceKnown && item.savings_reliable
                        ? '<small class="savings-badge ' + savingsClass(item.savings_percent) + '">Save ' + Number(item.savings_percent).toFixed(0) + '%</small>'
                        : (Number(item.estimated_savings_percent || 0) > 0
                            ? '<small class="savings-badge estimated">Est. save ' + Number(item.estimated_savings_percent).toFixed(0) + '%</small>'
                            : '') + '<small>Catalog price (unverified)</small>') +
                    (unitLabel ? '<small>' + escapeHtml(unitLabel) + '</small>' : '') +
                    '</div></a>';
            }).join('') + '</div>';

        // A published generic list price prices a different product. A per-unit
        // difference is shown only when the list and this brand's price are both current.
        const referenceHtml = !referenceOptions.length ? '' : '<div class="cheaper-reference">' +
            '<p class="eyebrow">Jan Aushadhi list price</p>' +
            '<p class="cheaper-reference-note">Published generic list price for a product with the same recorded ingredients, strength and form. It is not a listing of this brand, and it does not show that the products can be substituted.</p>' +
            '<div class="cheaper-list">' + referenceOptions.slice(0, 3).map(function (item) {
                const unit = item.unit || 'unit';
                const unitLabel = item.price_per_unit
                    ? 'Rs. ' + formatUnitPrice(item.price_per_unit) + ' per ' + unit
                    : '';
                const observed = formatDateValue(item.observed_at);
                const dateLabel = observed
                    ? 'List dated ' + observed + (item.price_is_current ? '' : ' (over a year old)')
                    : (item.edition ? 'Edition ' + item.edition + ', list date not published' : 'List date not published');
                return '<div class="cheaper-item cheaper-item-static">' +
                    '<div class="cheaper-copy"><strong>' + escapeHtml(item.product_name || 'Generic product') + '</strong>' +
                    '<small>' + escapeHtml(item.composition || 'Same recorded composition') + ' · ' + escapeHtml(item.pack || 'Pack unknown') + '</small>' +
                    '<small>' + escapeHtml(dateLabel) + '</small></div>' +
                    '<div class="cheaper-price"><span>Rs. ' + formatPrice(item.price) + '</span>' +
                    (unitLabel ? '<small>' + escapeHtml(unitLabel) + '</small>' : '') +
                    (item.price_is_current && Number(item.savings_percent || 0) > 0
                        ? '<small class="savings-badge ' + savingsClass(item.savings_percent) + '">' + Number(item.savings_percent).toFixed(0) + '% lower per unit</small>'
                        : Number(item.estimated_savings_percent || 0) > 0
                            ? '<small class="savings-badge estimated">Est. ' + Number(item.estimated_savings_percent).toFixed(0) + '% lower per unit</small>'
                            : '') +
                    '<small>Jan Aushadhi list price</small>' +
                    '</div></div>';
            }).join('') + '</div></div>';

        panel.innerHTML = catalogHtml + referenceHtml;
        panel.hidden = false;
    }

    function savingsClass(percent) {
        const value = Number(percent || 0);
        return value > 50 ? "high" : value > 25 ? "medium" : "low";
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

        count.textContent = state.filtered.length + (state.filtered.length === 1 ? " result" : " results");
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
            const isCurrent = Number(button.dataset.page) === state.page;
            button.classList.toggle("active", isCurrent);
            if (isCurrent) {
                button.setAttribute("aria-current", "page");
            } else {
                button.removeAttribute("aria-current");
            }
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
        const unmatchedPanel = document.getElementById("unmatchedPanel");
        const unmatchedList = document.getElementById("unmatchedList");
        const ocrTextPanel = document.getElementById("ocrTextPanel");
        const ocrText = document.getElementById("ocrText");
        const ocrProvider = document.getElementById("ocrProvider");
        const ocrProviderLabels = {
            "google-vision": "Google Vision",
            "tesseract": "Tesseract",
            "pdf-text-layer": "PDF text layer"
        };
        const statusRegion = document.getElementById("scanStatus");
        const reset = document.querySelector("[data-scan-reset]");
        let scanRequest = 0;

        function announce(message) {
            if (statusRegion) statusRegion.textContent = message;
        }

        ["dragover", "dragleave", "drop"].forEach(function (name) {
            uploadZone.addEventListener(name, function (event) {
                event.preventDefault();
                dropCard.classList.toggle("dragging", name === "dragover");
                if (name === "drop" && event.dataTransfer.files[0]) {
                    startScan(event.dataTransfer.files[0]);
                }
            });
        });

        const fileTrigger = uploadZone.querySelector("[data-file-trigger]");
        if (fileTrigger) {
            fileTrigger.addEventListener("click", function () {
                input.click();
            });
        }

        input.addEventListener("change", function () {
            if (input.files[0]) startScan(input.files[0]);
        });

        reset.addEventListener("click", function () {
            scanRequest += 1;
            input.value = "";
            dropCard.hidden = false;
            preview.hidden = true;
            detectedPanel.hidden = true;
            unmatchedPanel.hidden = true;
            ocrTextPanel.hidden = true;
            reset.hidden = true;
            detectedList.innerHTML = "";
            unmatchedList.innerHTML = "";
            ocrText.textContent = "";
            ocrProvider.textContent = "";
            previewFrame.innerHTML = '<span class="icon icon-file"></span>';
            announce("Scanner reset. Choose a prescription to scan.");
        });

        detectedList.addEventListener("click", function (event) {
            const remove = event.target.closest("[data-remove-detected]");
            if (!remove) return;
            const row = remove.closest("[data-detected]");
            if (row) row.remove();
            if (!detectedList.querySelector("[data-detected]")) {
                detectedList.innerHTML = emptyState("No medicines left", "Scan another prescription or search manually.");
            }
        });

        function showExtractedText(text, provider) {
            if (!text) {
                ocrTextPanel.hidden = true;
                return;
            }
            const label = String(provider || "")
                .split("+")
                .map(function (name) { return ocrProviderLabels[name] || name; })
                .filter(Boolean)
                .join(" + ");
            ocrText.textContent = text;
            ocrProvider.textContent = label ? " · " + label : "";
            ocrTextPanel.hidden = false;
        }

        async function startScan(file) {
            const requestId = ++scanRequest;
            dropCard.hidden = true;
            preview.hidden = false;
            detectedPanel.hidden = true;
            unmatchedPanel.hidden = true;
            ocrTextPanel.hidden = true;
            reset.hidden = true;
            previewFrame.classList.add("scanning");
            setText("scanStateLabel", "Scanning…");
            setText("scanTitle", "Reading your prescription");
            setText("scanCopy", "Reading the prescription and matching the catalog.");
            announce("Scanning the prescription and matching the catalog.");

            if (file.type.startsWith("image/")) {
                const url = URL.createObjectURL(file);
                previewFrame.innerHTML = '<img src="' + url + '" alt="Prescription preview">';
            } else {
                previewFrame.innerHTML = '<span class="icon icon-file"></span><small>' + escapeHtml(file.name) + '</small>';
            }

            const formData = new FormData();
            formData.append("file", file);
            const response = await fetchJson("/api/ocr", { method: "POST", body: formData });
            if (requestId !== scanRequest) return;

            previewFrame.classList.remove("scanning");
            reset.hidden = false;
            if (!response || response.success === false) {
                setText("scanStateLabel", "Not readable");
                setText("scanTitle", "Could not read this prescription");
                setText("scanCopy", (response && response.message) || "The OCR service did not respond. Try a clearer image or search manually.");
                announce("Could not read this prescription. " + ((response && response.message) || "Try a clearer image or search manually."));
                showExtractedText(response && response.text, response && response.provider);
                return;
            }

            setText("scanStateLabel", "Scan complete");
            setText("scanTitle", response.count ? "Medicines detected" : "No catalog match found");
            setText("scanCopy", response.message || "Review the detected items below.");

            const items = response.data || [];
            detectedList.innerHTML = items.length
                ? items.map(renderDetectedMedicine).join("")
                : emptyState("No medicines matched", "Check the printed lines below or search manually.");
            detectedPanel.hidden = false;

            const unmatched = response.unmatched_lines || [];
            unmatchedList.innerHTML = unmatched.map(renderUnmatchedLine).join("");
            unmatchedPanel.hidden = unmatched.length === 0;
            showExtractedText(response.text, response.provider);
            announce((items.length ? items.length + (items.length === 1 ? " medicine matched" : " medicines matched") : "No medicines matched") +
                (unmatched.length ? " and " + unmatched.length + (unmatched.length === 1 ? " printed line not matched." : " printed lines not matched.") : "."));
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
            messages.insertAdjacentHTML("beforeend", '<div class="chat-row ai typing-row"><span class="avatar-icon icon icon-bot"></span><div class="bubble">Thinking…</div></div>');
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
        return '<div class="medicine-card-wrapper">' +
            '<a class="medicine-card card-hover" href="/medicine/' + encodeURIComponent(m.id) + '">' +
            '<div class="medicine-visual ' + gradient + '">' +
            (m.rx ? '<span class="rx-badge">Rx</span>' : '') +
            '<span class="form-badge">' + escapeHtml(m.form || "Med") + '</span>' +
            '<span class="icon icon-pill"></span></div>' +
            '<div class="medicine-body">' +
            '<small>' + escapeHtml(m.brand) + '</small>' +
            '<h3>' + escapeHtml(m.name) + '</h3>' +
            '<small>' + escapeHtml(m.manufacturer || m.category || "MED FINDER") + '</small>' +
            '<div class="medicine-meta"><span>' + escapeHtml(m.category || "Medicine") + '</span></div>' +
            '<div class="price-row"><div><div class="price">Rs. ' + escapeHtml(formatPrice(m.price)) + '</div><div class="mrp">' + priceProvenance(m) + '</div></div><span class="view-pill">View</span></div>' +
            '</div></a>' +
            '<button class="btn btn-outline btn-compare" type="button" data-compare-id="' + m.id + '" onclick="event.preventDefault(); showComparison(' + m.id + ', \'' + escapeHtml(m.name).replace(/'/g, "\\'") + '\')"><span class="icon icon-shuffle"></span> Compare Prices</button>' +
            '</div>';
    }

    function renderMedicineRow(item, index) {
        const m = normalizeMedicine(item);
        const gradient = gradients[(Number(m.id) + (index || 0)) % gradients.length];
        return '<a class="result-row card-hover" href="/medicine/' + encodeURIComponent(m.id) + '">' +
            '<div class="medicine-visual ' + gradient + '"><span class="icon icon-pill"></span></div>' +
            '<div><h3>' + escapeHtml(m.name) + '</h3><p>' + escapeHtml(m.brand) + ' | ' + escapeHtml(m.composition || m.category || "Medicine") + '</p></div>' +
            '<div class="price-row"><div><div class="price">Rs. ' + escapeHtml(formatPrice(m.price)) + '</div><div class="mrp">' + priceProvenance(m) + '</div></div></div>' +
            '</a>';
    }

    function renderMedicineDetail(item) {
        const m = normalizeMedicine(item);
        const gradient = gradients[Number(m.id) % gradients.length];
        return '<section class="panel glass detail-visual">' +
            '<div class="medicine-art ' + gradient + '"><span class="icon icon-pill"></span>' + (m.rx ? '<span class="rx-badge">Prescription</span>' : '') + '</div>' +
            '<div class="detail-price-panel"><div class="price">Rs. ' + escapeHtml(formatPrice(m.price)) + '</div><div class="mrp">' + priceProvenance(m) + '</div></div>' +
            '</section>' +
            '<section><article class="panel surface-only detail-copy">' +
            '<div class="badge-row"><span class="badge">' + escapeHtml(m.category || "Medicine") + '</span><span class="badge secondary">' + escapeHtml(m.form || "Form") + '</span>' +
            (m.apiSource ? '<span class="badge secondary">Live catalog record</span>' : '') + '</div>' +
            '<h1>' + escapeHtml(m.name) + '</h1><p class="subline">' + escapeHtml(m.brand || "Brand") + ' | by ' + escapeHtml(m.manufacturer || "MED FINDER") + '</p>' +
            (m.apiSource ? '<p class="subline">Added from the ' + escapeHtml(m.apiSource) + '.' + (Number(m.price) > 0 ? ' The price shown is the live listing price from that catalog.' : ' No price is recorded for this product.') + '</p>' : '') +
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
        const details = [item.strength, item.dosage_form].filter(Boolean).join(" · ");
        const dosage = item.dosage_text || "Dosage not detected - confirm on the prescription";
        const printed = item.source_line ? "Printed: " + item.source_line : "Printed line unavailable";
        const medicineId = item.medicine_id || item.id;
        const exact = item.match_type === "exact";
        const close = item.match_type === "possible";
        const strengthDiffers = item.strength_match === false;
        const matchLabel = strengthDiffers
            ? "Possible match — check strength"
            : exact ? "Name match" : close ? "Close match — verify" : "Possible match";
        const badgeClass = strengthDiffers
            ? "warning"
            : exact ? "exact" : close ? "possible" : "partial";
        return '<div class="detected-item" data-detected><span class="feature-icon soft"><span class="icon icon-pill"></span></span>' +
            '<div class="min-grow"><strong>' + escapeHtml(name) + '</strong>' +
            (details ? '<small>' + escapeHtml(details) + '</small>' : '') +
            '<small>' + escapeHtml(dosage) + '</small>' +
            '<small class="ocr-source">' + escapeHtml(printed) + '</small></div>' +
            '<div class="detected-actions">' +
            '<span class="match-badge ' + badgeClass + '">' + matchLabel + '</span>' +
            (medicineId ? '<a class="btn btn-outline btn-sm" href="/medicine/' + encodeURIComponent(medicineId) + '">View</a>' : '') +
            '<a class="btn btn-outline btn-sm" href="/search?q=' + encodeURIComponent(item.matched_text || name) + '">Search</a>' +
            '<button class="btn btn-quiet btn-sm" type="button" data-remove-detected>Remove</button>' +
            '</div></div>';
    }

    function renderUnmatchedLine(item) {
        const printed = item.text || "Unreadable line";
        const query = item.query || printed;
        return '<div><span class="feature-icon soft"><span class="icon icon-file"></span></span>' +
            '<div class="min-grow"><strong>' + escapeHtml(printed) + '</strong><small>Not matched to a catalog medicine</small></div>' +
            '<div class="detected-actions"><a class="btn btn-outline btn-sm" href="/search?q=' + encodeURIComponent(query) + '">Search</a></div></div>';
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
            priceSource: item.price_source || "",
            priceCheckedAt: item.price_checked_at || "",
            packSize: item.pack_size || "",
            releaseType: item.release_type || "",
            category: item.medicine_category || item.category || "",
            apiSource: item.api_source || "",
            rx: String(item.prescription_required || item.rx || "").toLowerCase() === "true" || String(item.prescription_required || "").includes("Required")
        };
    }

    function priceProvenance(medicine) {
        if (!medicine.price) return "Price unavailable";
        const date = medicine.priceCheckedAt ? new Date(medicine.priceCheckedAt) : null;
        const dateLabel = date && !Number.isNaN(date.getTime())
            ? "Checked " + dateFormatter.format(date)
            : "update date unknown";
        return escapeHtml((medicine.priceSource || "Listed price") + " · " + dateLabel);
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
            const response = await fetch(url, Object.assign({ cache: "no-store" }, options || {}));
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

    const priceFormatter = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
    const unitPriceFormatter = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const dateFormatter = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" });

    function formatPrice(value) {
        const number = Number(value || 0);
        if (!number) return "-";
        return priceFormatter.format(number);
    }

    function formatUnitPrice(value) {
        return unitPriceFormatter.format(Number(value || 0));
    }

    function formatDateValue(value) {
        if (!value) return "";
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? "" : dateFormatter.format(parsed);
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
        function debounced() {
            const args = arguments;
            clearTimeout(timeout);
            timeout = setTimeout(function () {
                timeout = null;
                fn.apply(null, args);
            }, wait);
        }
        debounced.cancel = function () {
            clearTimeout(timeout);
            timeout = null;
        };
        return debounced;
    }

    // Price Comparison Feature
    let modalReturnFocus = null;

    function focusableIn(container) {
        return Array.from(container.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'))
            .filter(function (element) { return element.offsetParent !== null; });
    }

    window.showComparison = async function(medicineId, medicineName) {
        // Create modal if it doesn't exist
        let modal = document.getElementById("comparisonModal");
        if (!modal) {
            modal = document.createElement("div");
            modal.id = "comparisonModal";
            modal.className = "comparison-modal";
            modal.setAttribute("role", "dialog");
            modal.setAttribute("aria-modal", "true");
            modal.setAttribute("aria-labelledby", "comparisonTitle");
            modal.innerHTML = '<div class="modal-overlay" aria-hidden="true" onclick="closeComparison()"></div>' +
                '<div class="modal-content panel glass">' +
                '<div class="modal-header">' +
                '<h2><span class="icon icon-shuffle"></span> <span id="comparisonTitle">Price Comparison</span></h2>' +
                '<button class="btn-close" onclick="closeComparison()" aria-label="Close"><span class="icon icon-x"></span></button>' +
                '</div>' +
                '<div class="modal-body" id="comparisonBody">Loading…</div>' +
                '</div>';
            document.body.appendChild(modal);
            modal.addEventListener("keydown", function (event) {
                if (event.key !== "Tab") return;
                const focusable = focusableIn(modal);
                if (!focusable.length) return;
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                }
            });
        }

        // Show modal
        if (!modal.classList.contains("active")) {
            modalReturnFocus = document.activeElement;
        }
        modal.classList.add("active");
        document.body.style.overflow = "hidden";
        const closeButton = modal.querySelector(".btn-close");
        if (closeButton) closeButton.focus();

        // Update title
        document.getElementById("comparisonTitle").innerHTML = 'Alternatives for <strong>' + escapeHtml(medicineName) + '</strong>';

        // Fetch substitutes
        const body = document.getElementById("comparisonBody");
        body.innerHTML = '<div class="loading-state"><div class="spinner"></div><p>Checking verified alternatives…</p></div>';

        const responses = await Promise.all([
            fetchJson("/api/substitutes/" + encodeURIComponent(medicineId) + "?limit=10"),
            fetchJson("/api/live-alternatives/" + encodeURIComponent(medicineId) + "?limit=10"),
            fetchJson("/api/price-alternatives/" + encodeURIComponent(medicineId) + "?limit=10")
        ]);
        const data = responses[0];
        const liveApiData = responses[1];
        const livePriceData = responses[2];
        const renderLiveApiMatches = function () {
            if (!liveApiData) return '<section class="comparison-section live-api-section"><h3>Live India API lookup</h3><p class="disclaimer">The live medicine information API could not be reached. Try again later.</p></section>';
            const matches = Array.isArray(liveApiData.matches) ? liveApiData.matches : [];
            if (!matches.length) return '<section class="comparison-section live-api-section"><h3>Live India API lookup</h3><p>' + escapeHtml(liveApiData.message || 'No structured live catalog matches were returned.') + '</p><small>Source: ' + escapeHtml(liveApiData.source || 'India medicine API') + '</small></section>';
            return '<section class="comparison-section live-api-section">' +
                '<h3>Live India API catalog matches <span class="count-badge">' + matches.length + '</span></h3>' +
                '<p class="disclaimer">These products share recorded ingredient, strength and form details in the live catalog. The API does not confirm product-level therapeutic equivalence or provide current prices.</p>' +
                '<div class="substitutes-list">' + matches.map(function (item) {
                    return '<article class="comparison-item substitute unverified-match">' +
                        '<div class="comparison-header"><div class="flex-grow"><h4>' + escapeHtml(item.medicine_name || 'Catalog medicine') + '</h4>' +
                        '<p>' + escapeHtml(item.manufacturer || item.brand_name || '') + '</p></div></div>' +
                        '<div class="comparison-details">' +
                        '<span class="badge">' + escapeHtml(item.dosage_form || 'Form unavailable') + '</span>' +
                        '<span class="comparison-source">Recorded generic: ' + escapeHtml(item.composition || 'Composition unavailable') + '</span>' +
                        '<span class="badge secondary">' + escapeHtml(item.release_characteristics || 'Release data unavailable') + '</span>' +
                        '<span class="badge secondary">' + escapeHtml(item.route || 'Route unavailable') + '</span>' +
                        '<span class="badge">Price unavailable from this API</span>' +
                        '<span class="comparison-source">License status: ' + escapeHtml(item.license_status || 'Not stated') + '</span>' +
                        '<span class="comparison-source">Catalog updated: ' + escapeHtml(item.product_updated_at || 'Date unknown') + '</span>' +
                        '</div><p class="verification-gaps">' + escapeHtml(item.verification_note || item.classification || 'Therapeutic equivalence is not confirmed.') + '</p>' +
                        '<div class="comparison-actions"><a class="btn btn-outline btn-sm" href="' + escapeHtml(item.source_url) + '" target="_blank" rel="noopener noreferrer">View source record</a></div>' +
                        '</article>';
                }).join('') + '</div><small>Source: ' + escapeHtml(liveApiData.source || 'India medicine API') + '</small></section>';
        };
        const renderLivePriceMatches = function () {
            if (!livePriceData) return '<section class="comparison-section live-price-section"><h3>Live price comparison</h3><p>Current pharmacy prices could not be loaded.</p></section>';
            const listings = Array.isArray(livePriceData.matches) ? livePriceData.matches : [];
            if (!listings.length) return '<section class="comparison-section live-price-section"><h3>Live price comparison</h3><p>' + escapeHtml(livePriceData.message || 'No comparable current listings were found.') + '</p></section>';
            const hasSavingsComparison = Boolean(livePriceData.comparison_available && livePriceData.reference);
            const reference = livePriceData.reference;
            return '<section class="comparison-section live-price-section">' +
                '<h3>' + (hasSavingsComparison ? 'Cheaper same-ingredient listings' : 'Current same-ingredient listings') +
                ' <span class="count-badge">' + listings.length + '</span></h3>' +
                '<p>' + escapeHtml(livePriceData.message || '') + '</p>' +
                (hasSavingsComparison ? '<p><strong>Reference listing:</strong> ' + escapeHtml(reference.medicine_name) +
                    ' · Rs. ' + formatPrice(reference.price) + ' · ' + escapeHtml(reference.pack_size || 'Pack unknown') + '</p>' : '') +
                '<div class="substitutes-list">' + listings.map(function (item) {
                    return '<article class="comparison-item substitute">' +
                        '<div class="comparison-header"><div class="flex-grow"><h4>' + escapeHtml(item.medicine_name || 'Pharmacy listing') + '</h4>' +
                        '<p>' + escapeHtml(item.manufacturer || item.brand_name || '') + '</p></div>' +
                        '<div class="comparison-price"><span class="price-large">Rs. ' + formatPrice(item.price) + '</span>' +
                        (item.savings_percent > 0 ? '<span class="savings-badge low">Save ' + Number(item.savings_percent).toFixed(0) + '%</span>' : '') +
                        '</div></div>' +
                        '<div class="comparison-details"><span class="badge">' + escapeHtml(item.pack_size || 'Pack unknown') + '</span>' +
                        (item.price_per_unit ? '<span class="badge secondary">' + escapeHtml(item.price_per_unit) + '</span>' : '') +
                        '<span class="comparison-source">' + escapeHtml(item.source || 'Pharmacy') + ' · checked ' + escapeHtml(item.checked_at || livePriceData.checked_at || 'just now') + '</span>' +
                        (item.source_url ? '<a class="comparison-source" href="' + escapeHtml(item.source_url) + '" target="_blank" rel="noopener noreferrer">View listing</a>' : '') +
                        '</div></article>';
                }).join('') + '</div></section>';
        };

        const unverifiedMatches = data && Array.isArray(data.unverified_matches) ? data.unverified_matches : [];
        const sameComposition = data && Array.isArray(data.same_composition_alternatives) ? data.same_composition_alternatives : [];
        const sameOptions = data && Array.isArray(data.same_composition_options) ? data.same_composition_options : [];
        const referenceOptions = data && Array.isArray(data.reference_price_options) ? data.reference_price_options.filter(function (item) {
            return Number(item.price || 0) > 0;
        }) : [];
        const sameIds = new Set(sameComposition.map(function (item) { return String(item.id); }));
        const optionIds = new Set(sameOptions.map(function (item) { return String(item.id); }));
        const renderReferencePrices = function () {
            if (!referenceOptions.length) return '';
            return '<section class="comparison-section reference-price-section">' +
                '<h3>Jan Aushadhi list price for the same composition <span class="count-badge">' + referenceOptions.length + '</span></h3>' +
                '<p class="disclaimer">A published generic list price for products with the same recorded ingredients, strength and form. It prices a generic product, not this brand, and does not show that the products can be substituted.</p>' +
                '<div class="substitutes-list">' + referenceOptions.map(function (item) {
                    const unit = item.unit || 'unit';
                    const unitLabel = item.price_per_unit
                        ? 'Rs. ' + formatUnitPrice(item.price_per_unit) + ' per ' + unit
                        : '';
                    const observed = formatDateValue(item.observed_at);
                    const dateLabel = observed
                        ? 'List dated ' + observed + (item.price_is_current ? '' : ' - over a year old')
                        : (item.edition ? 'Edition ' + item.edition + ', list date not published' : 'List date not published');
                    const difference = Number(item.difference_per_unit || 0);
                    const differenceLabel = !item.reference_price_per_unit || !difference
                        ? ''
                        : difference > 0
                            ? 'Rs. ' + formatUnitPrice(difference) + ' per ' + unit + ' below the recorded price of this product'
                            : 'Rs. ' + formatUnitPrice(-difference) + ' per ' + unit + ' above the recorded price of this product';
                    const percent = Number(item.savings_percent || 0);
                    return '<article class="comparison-item substitute">' +
                        '<div class="comparison-header"><div class="flex-grow">' +
                        '<h4>' + escapeHtml(item.product_name || 'Generic product') + '</h4>' +
                        '<p>' + escapeHtml(item.composition || 'Same recorded composition') + '</p></div>' +
                        '<div class="comparison-price"><span class="price-large">Rs. ' + formatPrice(item.price) + '</span>' +
                        (percent > 0 && item.price_is_current
                            ? '<span class="savings-badge ' + savingsClass(percent) + '">Save ' + percent.toFixed(0) + '%</span>'
                            : Number(item.estimated_savings_percent || 0) > 0
                                ? '<span class="savings-badge estimated">Est. save ' + Number(item.estimated_savings_percent).toFixed(0) + '%</span>'
                                : '<span class="comparison-source">List price</span>') +
                        '</div></div>' +
                        '<div class="comparison-details">' +
                        '<span class="badge">' + escapeHtml(item.pack || 'Pack unknown') + '</span>' +
                        (unitLabel ? '<span class="badge secondary">' + escapeHtml(unitLabel) + '</span>' : '') +
                        (differenceLabel ? '<span class="comparison-source">' + escapeHtml(differenceLabel) + '</span>' : '') +
                        '<span class="comparison-source">' + escapeHtml(item.source || 'Jan Aushadhi (PMBJP)') + ' · ' + escapeHtml(dateLabel) + '</span>' +
                        '</div></article>';
                }).join('') + '</div></section>';
        };
        const renderSameComposition = function () {
            if (!sameComposition.length) return '';
            return '<section class="comparison-section same-composition-section">' +
                '<h3>Cheaper same-composition options <span class="count-badge">' + sameComposition.length + '</span></h3>' +
                '<p class="disclaimer">Same recorded active ingredients and strength, with current listing prices on both sides. These are catalog matches, not verified therapeutic equivalents; prices are compared per unit when pack sizes differ.</p>' +
                '<div class="substitutes-list">' + sameComposition.map(function (item, idx) {
                    const percent = Number(item.savings_percent || 0);
                    const unit = item.price_per_unit_value
                        ? 'Rs. ' + formatUnitPrice(item.price_per_unit_value) + ' per unit'
                        : '';
                    return '<article class="comparison-item substitute">' +
                        '<div class="comparison-header">' +
                        '<div class="rank-badge">#' + (idx + 1) + '</div>' +
                        '<div class="flex-grow"><h4>' + escapeHtml(item.medicine_name) + '</h4>' +
                        '<p>' + escapeHtml(item.brand_name || item.composition || 'Same recorded composition') + '</p></div>' +
                        '<div class="comparison-price"><span class="price-large">Rs. ' + formatPrice(item.price) + '</span>' +
                        (percent > 0 && item.savings_reliable
                            ? '<span class="savings-badge ' + savingsClass(percent) + '">Save ' + percent.toFixed(0) + '%</span>'
                            : '<span class="comparison-source">Lower catalog price</span>') +
                        '</div></div>' +
                        '<div class="comparison-details">' +
                        '<span class="badge">' + escapeHtml(item.dosage_form || 'Form') + '</span>' +
                        '<span class="badge">' + escapeHtml(item.strength || 'Strength') + '</span>' +
                        '<span class="badge">' + escapeHtml(item.pack_size || 'Pack unknown') + '</span>' +
                        (unit ? '<span class="badge secondary">' + escapeHtml(unit) + '</span>' : '') +
                        '<span class="comparison-source">' + priceProvenance(normalizeMedicine(item)) + '</span>' +
                        (item.savings_amount > 0 && item.savings_reliable
                            ? '<span class="savings-text">Save Rs. ' + formatPrice(item.savings_amount) + (item.savings_basis === 'per unit' ? ' per unit' : ' per pack') + '</span>'
                            : '') +
                        (item.savings_reliable ? '' : '<span class="comparison-source">Catalog price - confirm current price</span>') +
                        '</div>' +
                        '<div class="comparison-actions"><a href="/medicine/' + encodeURIComponent(item.id) + '" class="btn btn-outline btn-sm">View Details</a></div>' +
                        '</article>';
                }).join('') + '</div></section>';
        };
        const renderSameCompositionOptions = function () {
            if (!sameOptions.length) return '';
            const noClaimNote = data && !data.reference_price_available
                ? 'This product has no recorded price, so no cheaper-than-reference saving can be claimed.'
                : data && data.reference_price_is_current
                    ? 'This price is a current listing, but no same-composition product with a current listing price is cheaper, so no saving is claimed.'
                        : sameOptions.some(function (item) { return Number(item.estimated_savings_percent || 0) > 0; })
                        ? 'The recorded prices are not confirmed current listings, so savings shown are estimates from catalog prices per unit; check the price at your pharmacy.'
                        : 'The recorded price for this product is not a confirmed current listing, so no saving is claimed.';
            return '<section class="comparison-section same-composition-section">' +
                '<h3>Same-composition catalog options <span class="count-badge">' + sameOptions.length + '</span></h3>' +
                '<p class="disclaimer">' + noClaimNote + ' These catalog products share the recorded active ingredients, strength and form; their catalog prices are unverified and they are not verified therapeutic equivalents.</p>' +
                '<div class="substitutes-list">' + sameOptions.map(function (item, idx) {
                    const unit = item.price_per_unit_value
                        ? 'Rs. ' + formatUnitPrice(item.price_per_unit_value) + ' per unit'
                        : '';
                    return '<article class="comparison-item substitute">' +
                        '<div class="comparison-header">' +
                        '<div class="rank-badge">#' + (idx + 1) + '</div>' +
                        '<div class="flex-grow"><h4>' + escapeHtml(item.medicine_name) + '</h4>' +
                        '<p>' + escapeHtml(item.brand_name || item.composition || 'Same recorded composition') + '</p></div>' +
                        '<div class="comparison-price"><span class="price-large">Rs. ' + formatPrice(item.price) + '</span>' +
                        (Number(item.estimated_savings_percent || 0) > 0
                            ? '<span class="savings-badge estimated">Est. save ' + Number(item.estimated_savings_percent).toFixed(0) + '%</span>'
                            : '') +
                        '<span class="comparison-source">Catalog price (unverified)</span></div></div>' +
                        '<div class="comparison-details">' +
                        '<span class="badge">' + escapeHtml(item.dosage_form || 'Form') + '</span>' +
                        '<span class="badge">' + escapeHtml(item.strength || 'Strength') + '</span>' +
                        '<span class="badge">' + escapeHtml(item.pack_size || 'Pack unknown') + '</span>' +
                        (unit ? '<span class="badge secondary">' + escapeHtml(unit) + '</span>' : '') +
                        '<span class="comparison-source">' + priceProvenance(normalizeMedicine(item)) + '</span>' +
                        '</div>' +
                        '<div class="comparison-actions"><a href="/medicine/' + encodeURIComponent(item.id) + '" class="btn btn-outline btn-sm">View Details</a></div>' +
                        '</article>';
                }).join('') + '</div></section>';
        };
        const renderUnverifiedMatches = function (matches) {
            matches = matches.filter(function (item) {
                return !sameIds.has(String(item.id)) && !optionIds.has(String(item.id));
            });
            if (!matches.length) return '';
            return '<section class="comparison-section unverified-section">' +
                '<h3>Same-ingredient catalog matches <span class="count-badge">' + Number(data.potential_match_count || matches.length) + '</span></h3>' +
                '<p class="disclaimer">These records are shown for review only. A match does not establish that products can be substituted. Prices below may be old dataset listings.</p>' +
                '<div class="substitutes-list">' + matches.map(function (item) {
                    const priceLabel = Number(item.price || 0) > 0
                        ? 'Catalog price: Rs. ' + formatPrice(item.price) + ' · ' + priceProvenance(normalizeMedicine(item))
                        : 'No catalog price';
                    return '<article class="comparison-item substitute unverified-match">' +
                        '<div class="comparison-header"><div class="flex-grow"><h4>' + escapeHtml(item.medicine_name) + '</h4>' +
                        '<p>' + escapeHtml(item.brand_name || item.composition || 'Same recorded ingredients') + '</p></div></div>' +
                        '<div class="comparison-details">' +
                        '<span class="badge">' + escapeHtml(item.dosage_form || 'Form unknown') + '</span>' +
                        '<span class="badge">' + escapeHtml(item.strength || 'Strength unknown') + '</span>' +
                        '<span class="badge secondary">' + escapeHtml(item.release_type || 'Release not verified') + '</span>' +
                        '<span class="badge">' + escapeHtml(item.pack_size || 'Pack size unknown') + '</span>' +
                        '<span class="comparison-source">' + escapeHtml(priceLabel) + '</span>' +
                        '</div><p class="verification-gaps"><strong>Needs checking:</strong> ' + escapeHtml((item.verification_gaps || ['Product evidence unavailable']).join('; ')) + '</p>' +
                        '<div class="comparison-actions"><a href="/medicine/' + encodeURIComponent(item.id) + '" class="btn btn-outline btn-sm">View Details</a></div>' +
                        '</article>';
                }).join('') + '</div></section>';
        };

        if (!data || !data.success || !data.substitutes || data.substitutes.length === 0) {
            const title = data && data.comparison_status === "restricted_class"
                ? "Cheaper options are not shown"
                : data && data.comparison_status === "insufficient_product_data"
                ? "Product data is incomplete"
                : data && data.comparison_status === "cheaper_same_composition"
                    ? "Cheaper same-composition options"
                    : data && data.comparison_status === "reference_price_unknown"
                        ? "Lowest-priced same-composition options"
                        : data && data.comparison_status === "catalog_price_unverified"
                            ? "Same-composition catalog options"
                            : data && data.comparison_status === "reference_pack_unknown"
                                ? "Same-composition catalog options"
                                : "No verified alternatives found";
            const explanation = data && data.message
                ? data.message
                : "No product-specific verified alternatives with recent comparable prices are available.";
            const sameHtml = renderSameComposition() + renderSameCompositionOptions() + renderReferencePrices();
            const emptyHtml = sameComposition.length || sameOptions.length || referenceOptions.length ? '' : '<div class="empty-state">' +
                '<span class="feature-icon"><span class="icon icon-package"></span></span>' +
                '<h3>' + escapeHtml(title) + '</h3>' +
                '<p>' + escapeHtml(explanation) + '</p>' +
                '</div>';
            body.innerHTML = emptyHtml + sameHtml + renderUnverifiedMatches(unverifiedMatches) + renderLiveApiMatches() + renderLivePriceMatches();
            return;
        }

        // Render comparison results
        const original = data.original_medicine;
        const substitutes = data.substitutes;
        const maxSavings = data.max_savings_percent || 0;


        body.innerHTML = '<div class="comparison-summary">' +
            '<div class="summary-card success">' +
            '<span class="icon icon-trending-down"></span>' +
            '<div><strong>Verified price difference up to ' + maxSavings.toFixed(0) + '%</strong><small>Same recorded pack size</small></div>' +
            '</div>' +
            '<div class="summary-card info">' +
            '<span class="icon icon-package"></span>' +
            '<div><strong>' + substitutes.length + ' evidence-verified alternatives' + '</strong><small>Same recorded ingredients, strength, form and pack</small></div>' +
            '</div>' +
            '</div>' +
            '<div class="comparison-section">' +
            '<h3>Original Medicine</h3>' +
            '<div class="comparison-item original">' +
            '<div class="comparison-header">' +
            '<div><h4>' + escapeHtml(original.medicine_name) + '</h4>' +
            '<p>' + escapeHtml(original.brand_name || original.composition || 'Standard composition') + '</p></div>' +
            '<div class="comparison-price"><span class="price-large">Rs. ' + formatPrice(original.price) + '</span></div>' +
            '</div>' +
            '<div class="comparison-details">' +
            '<span class="badge">' + escapeHtml(original.medicine_category || 'Medicine') + '</span>' +
            '<span class="badge secondary">' + escapeHtml(original.dosage_form || 'Standard form') + '</span>' +
            '<span class="badge">' + escapeHtml(original.strength || 'Standard strength') + '</span>' +
            '<span class="badge secondary">' + escapeHtml(original.release_type || 'Release not verified') + '</span>' +
            '<span class="badge">' + escapeHtml(original.pack_size || 'Pack size unknown') + '</span>' +
            '<span class="comparison-source">' + priceProvenance(normalizeMedicine(original)) + '</span>' +
            '</div>' +
            '</div>' +
            '</div>' +
            '<div class="comparison-section">' +
            '<h3>Evidence-verified alternatives <span class="count-badge">' + substitutes.length + '</span></h3>' +
            '<div class="substitutes-list">' +
            substitutes.map(function(sub, idx) {
                const savingsPercent = sub.savings_percent || 0;
                const savingsAmount = sub.savings_amount || 0;
                const savingsClass = savingsPercent > 50 ? 'high' : savingsPercent > 25 ? 'medium' : 'low';

                return '<div class="comparison-item substitute">' +
                    '<div class="comparison-header">' +
                    '<div class="rank-badge">#' + (idx + 1) + '</div>' +
                    '<div class="flex-grow">' +
                    '<h4>' + escapeHtml(sub.medicine_name) + '</h4>' +
                    '<p>' + escapeHtml(sub.brand_name || sub.composition || 'Same composition') + '</p>' +
                    '</div>' +
                    '<div class="comparison-price">' +
                    '<span class="price-large">Rs. ' + formatPrice(sub.price) + '</span>' +
                    (savingsPercent > 0 ? '<span class="savings-badge ' + savingsClass + '">Save ' + savingsPercent.toFixed(0) + '%</span>' : '') +
                    '</div>' +
                    '</div>' +
                    '<div class="comparison-details">' +
                    '<span class="badge">' + escapeHtml(sub.medicine_category || 'Medicine') + '</span>' +
                    '<span class="badge secondary">' + escapeHtml(sub.dosage_form || 'Form') + '</span>' +
                    '<span class="badge">' + escapeHtml(sub.strength || 'Strength') + '</span>' +
                    '<span class="badge secondary">' + escapeHtml(sub.release_type || 'Release not verified') + '</span>' +
                    '<span class="badge">' + escapeHtml(sub.pack_size || 'Pack size unknown') + '</span>' +
                    (sub.match_classification ? '<span class="badge secondary">' + escapeHtml(sub.match_classification) + '</span>' : '') +
                    '<span class="comparison-source">' + priceProvenance(normalizeMedicine(sub)) + '</span>' +
                    (sub.equivalence_evidence && sub.equivalence_evidence.source_url ? '<a class="comparison-source" href="' + escapeHtml(sub.equivalence_evidence.source_url) + '" target="_blank" rel="noopener noreferrer">View equivalence evidence</a>' : '') +
                    (savingsAmount > 0 ? '<span class="savings-text">You save Rs. ' + formatPrice(savingsAmount) + '</span>' : '') +
                    '</div>' +
                    '<div class="comparison-actions">' +
                    '<a href="/medicine/' + sub.id + '" class="btn btn-outline btn-sm">View Details</a>' +
                    '</div>' +
                    '</div>';
            }).join('') +
            '</div>' +
            '</div>' +
            '<div class="comparison-footer">' +
            '<p class="disclaimer"><span class="icon icon-alert-circle"></span> Evidence and recent price checks are shown for each verified result. Ask a pharmacist or prescriber before changing treatment.</p>' +
            '</div>' + renderSameComposition() + renderSameCompositionOptions() + renderReferencePrices() + renderUnverifiedMatches(unverifiedMatches) + renderLiveApiMatches() + renderLivePriceMatches();
    };

    window.closeComparison = function() {
        const modal = document.getElementById("comparisonModal");
        if (modal) {
            modal.classList.remove("active");
            document.body.style.overflow = "";
        }
        if (modalReturnFocus && document.contains(modalReturnFocus)) {
            modalReturnFocus.focus();
        }
        modalReturnFocus = null;
    };

    // Close modal on Escape key
    document.addEventListener("keydown", function(event) {
        if (event.key === "Escape") {
            closeComparison();
        }
    });
})();
