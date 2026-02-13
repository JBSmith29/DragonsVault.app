(function () {
  function initMagicRules() {
    const root = document.getElementById('rulesApp');
    if (!root || root.dataset.bound === 'true') return;
    root.dataset.bound = 'true';
      const searchInput = root.querySelector('#rulesSearchInput');
      const searchBtn = root.querySelector('#rulesSearchBtn');
      const clearBtn = root.querySelector('#rulesClearBtn');
      const loadBtn = root.querySelector('#rulesLoadBtn');
      const expandBtn = root.querySelector('#rulesExpandAllBtn');
      const collapseBtn = root.querySelector('#rulesCollapseAllBtn');
      const filterBtn = root.querySelector('#rulesFilterToggle');
      const jumpInput = root.querySelector('#rulesJumpInput');
      const jumpBtn = root.querySelector('#rulesJumpBtn');
      const statusEl = root.querySelector('#rulesSearchStatus');
      const resultsEl = root.querySelector('#rulesSearchResults');
      const navEl = root.querySelector('#rulesNav');
      const workbookEl = root.querySelector('#rulesWorkbook');
      const workbookStatusEl = root.querySelector('#rulesWorkbookStatus');
      const navStateKey = 'dv_rules_nav_state';
      const prefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      let rulesData = [];
      let ruleMap = new Map();
      let workbookReady = false;
      let lastQuery = '';
      let lastMatches = [];
    
      const rulesUrl = root.dataset.rulesUrl || '';
      const rulesApiUrl = root.dataset.rulesApiUrl || '';
      const rulesWorkbookApiUrl = root.dataset.rulesWorkbookApiUrl || '';
      const rulesSearchApiUrl = root.dataset.rulesSearchApiUrl || '';
      const rulesStorageKey = `dv_rules_${(rulesUrl || 'magic_rules').split('/').pop()}`;
      const rulesStorageMetaKey = `${rulesStorageKey}_meta`;
      const inlineWorkbookEl = root.querySelector('#rulesWorkbookInline');
      let inlineWorkbookData = null;
      if (inlineWorkbookEl) {
        try {
          inlineWorkbookData = JSON.parse(inlineWorkbookEl.textContent || '[]');
        } catch (err) {
          console.error('Failed to parse inline workbook JSON', err);
          inlineWorkbookData = null;
        }
      }
      const hasInlineWorkbook = Array.isArray(inlineWorkbookData) && inlineWorkbookData.length > 0;
    
      function slugify(text) {
        return String(text || '')
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, '-')
          .replace(/^-+|-+$/g, '')
          .slice(0, 64) || 'section';
      }
    
      function parseRules(text) {
        const lines = text.replace(/^\ufeff/, '').split(/\r\n|\n|\r/);
        const chapters = [];
        let currentChapter = null;
        let currentSub = null;
        let introBucket = null;
        let inContents = false;
        let contentsEnded = false;
        let lastRule = null;
        let inGlossary = false;
        let inCredits = false;
        let glossaryCurrent = null;
    
        const chapterRe = /^(\d+)\.\s+(.+)/;
        const subRe = /^(\d{3})\.\s+(.+)/;
        const ruleRe = /^(\d{3}\.\d+[a-z]?)\b/;
        const glossaryTermRe = /^[A-Za-z0-9"()\[\]/:+,&\s-]+$/;
        const glossaryNumberedRe = /^\d+\./;
    
        const ensureChapter = (title) => {
          const existing = chapters.find((chapter) => chapter.title === title);
          if (existing) {
            currentChapter = existing;
            currentSub = null;
            return existing;
          }
          const chapter = {
            title,
            id: `chapter-${slugify(title)}`,
            sections: [],
          };
          chapters.push(chapter);
          currentChapter = chapter;
          currentSub = null;
          return chapter;
        };
    
        const ensureSub = (title) => {
          if (!currentChapter) {
            currentChapter = ensureChapter('Miscellaneous');
          }
          const existing = currentChapter.sections.find((section) => section.title === title);
          if (existing) {
            currentSub = existing;
            return existing;
          }
          const section = {
            title,
            id: `section-${slugify((currentChapter.title || '') + '-' + title)}`,
            rules: [],
            notes: [],
          };
          currentChapter.sections.push(section);
          currentSub = section;
          return section;
        };
    
        for (let i = 0; i < lines.length; i += 1) {
          const raw = lines[i] || '';
          const line = raw.trim();
          if (!line) {
            continue;
          }
          if (line === 'Contents') {
            inContents = true;
            contentsEnded = false;
            continue;
          }
          if (inContents) {
            if (line === 'Credits') {
              contentsEnded = true;
            }
            if (contentsEnded && chapterRe.test(line)) {
              inContents = false;
            } else if (line === 'Glossary' || line === 'Credits' || chapterRe.test(line)) {
              continue;
            } else {
              continue;
            }
          }
    
          if (line === 'Introduction') {
            introBucket = ensureChapter('Introduction');
            ensureSub('Overview');
            lastRule = null;
            inGlossary = false;
            inCredits = false;
            glossaryCurrent = null;
            continue;
          }
          if (line === 'Glossary') {
            ensureChapter('Glossary');
            ensureSub('Glossary');
            lastRule = null;
            inGlossary = true;
            inCredits = false;
            glossaryCurrent = null;
            continue;
          }
          if (line === 'Credits') {
            ensureChapter('Credits');
            ensureSub('Credits');
            lastRule = null;
            inGlossary = false;
            inCredits = true;
            glossaryCurrent = null;
            continue;
          }
    
          if (inGlossary) {
            const isTerm = !glossaryNumberedRe.test(line)
              && !line.endsWith('.')
              && glossaryTermRe.test(line);
            if (isTerm) {
              const existing = currentSub.rules.find((rule) => rule.number === line);
              if (existing) {
                glossaryCurrent = existing;
              } else {
                glossaryCurrent = {
                  id: `rule-glossary-${slugify(line)}`,
                  number: line,
                  text: line,
                  notes: [],
                  kind: 'glossary',
                };
                currentSub.rules.push(glossaryCurrent);
              }
            } else {
              if (!glossaryCurrent) {
                glossaryCurrent = {
                  id: `rule-glossary-${slugify('entry')}-${currentSub.rules.length + 1}`,
                  number: 'Glossary Entry',
                  text: 'Glossary Entry',
                  notes: [],
                  kind: 'glossary',
                };
                currentSub.rules.push(glossaryCurrent);
              }
              const lastNote = glossaryCurrent.notes[glossaryCurrent.notes.length - 1];
              if (line !== lastNote) {
                glossaryCurrent.notes.push(line);
              }
            }
            continue;
          }
    
          if (inCredits) {
            const lastNote = currentSub.notes[currentSub.notes.length - 1];
            if (line !== lastNote) {
              currentSub.notes.push(line);
            }
            continue;
          }
    
          let match = subRe.exec(line);
          if (match) {
            ensureSub(`${match[1]}. ${match[2]}`);
            lastRule = null;
            continue;
          }
          match = chapterRe.exec(line);
          if (match) {
            ensureChapter(`${match[1]}. ${match[2]}`);
            lastRule = null;
            continue;
          }
          match = ruleRe.exec(line);
          if (match) {
            if (!currentSub) {
              if (!currentChapter) {
                currentChapter = ensureChapter('Rules');
              }
              currentSub = ensureSub('General');
            }
            const existingRule = currentSub.rules.find((rule) => rule.number === match[1]);
            if (existingRule) {
              lastRule = existingRule;
              continue;
            }
            const rule = {
              id: `rule-${match[1].replace(/\./g, '-')}`,
              number: match[1],
              text: line,
              notes: [],
            };
            currentSub.rules.push(rule);
            lastRule = rule;
            continue;
          }
    
          if (!currentSub) {
            if (!currentChapter) {
              currentChapter = introBucket || ensureChapter('Introduction');
            }
            currentSub = ensureSub('Overview');
          }
    
          if (currentChapter && line === currentChapter.title) {
            continue;
          }
          if (currentSub && line === currentSub.title) {
            continue;
          }
    
          if (currentSub.rules && currentSub.rules.length) {
            if (!lastRule) {
              lastRule = currentSub.rules[currentSub.rules.length - 1];
            }
            if (lastRule) {
              const lastNote = lastRule.notes && lastRule.notes[lastRule.notes.length - 1];
              if (line !== lastNote) {
                lastRule.notes.push(line);
              }
              continue;
            }
          }
    
          const lastSectionNote = currentSub.notes[currentSub.notes.length - 1];
          if (line !== lastSectionNote) {
            currentSub.notes.push(line);
          }
        }
    
        return chapters;
      }
    
      function buildRuleMap(chapters) {
        const map = new Map();
        chapters.forEach((chapter) => {
          chapter.sections.forEach((section) => {
            section.rules.forEach((rule) => {
              map.set(rule.number, rule);
            });
          });
        });
        return map;
      }

      function buildWorkbookFromDOM() {
        if (!workbookEl) return [];
        const chapters = [];
        const chapterNodes = workbookEl.querySelectorAll(':scope > .rules-card');
        chapterNodes.forEach((chapterEl) => {
          const titleEl = chapterEl.querySelector('h2');
          const title = titleEl ? titleEl.textContent.trim() : chapterEl.id || 'Rules';
          const chapter = {
            title,
            id: chapterEl.id || `chapter-${slugify(title)}`,
            sections: [],
          };
          const sectionNodes = chapterEl.querySelectorAll(':scope > details.rules-section');
          sectionNodes.forEach((sectionEl) => {
            const summaryTitle = sectionEl.querySelector('.rules-section-title span');
            const sectionTitle = summaryTitle ? summaryTitle.textContent.trim() : sectionEl.id || 'Section';
            const section = {
              title: sectionTitle,
              id: sectionEl.id || `section-${slugify(`${title}-${sectionTitle}`)}`,
              rules: [],
              notes: [],
            };
            sectionEl.querySelectorAll('.rules-section-notes > div').forEach((noteEl) => {
              const noteText = noteEl.textContent.trim();
              if (noteText) section.notes.push(noteText);
            });
            sectionEl.querySelectorAll('.rule-line').forEach((ruleEl) => {
              const number = (ruleEl.dataset.ruleNumber || '').trim();
              const textEl = ruleEl.querySelector('.rule-text');
              const text = textEl ? textEl.textContent.trim() : ruleEl.textContent.trim();
              const notes = [];
              ruleEl.querySelectorAll('.rule-note').forEach((noteEl) => {
                const noteText = noteEl.textContent.trim();
                if (noteText) notes.push(noteText);
              });
              const numberMatch = !number && text ? text.match(/^\s*(\d{3}\.\d+[a-z]?)/i) : null;
              const normalizedNumber = number || (numberMatch ? numberMatch[1] : '');
              section.rules.push({
                id: ruleEl.id || `rule-${slugify(normalizedNumber || text)}`,
                number: normalizedNumber,
                text,
                notes,
              });
            });
            chapter.sections.push(section);
          });
          chapters.push(chapter);
        });
        return chapters;
      }

      function hydrateFromDOM() {
        if (!workbookEl) return false;
        if (!workbookEl.querySelector('.rule-line')) return false;
        const chapters = buildWorkbookFromDOM();
        if (!chapters.length) return false;
        rulesData = chapters;
        ruleMap = buildRuleMap(chapters);
        workbookReady = true;
        if (workbookStatusEl) {
          workbookStatusEl.textContent = 'Rules ready. Use search or the section list to navigate.';
        }
        if (statusEl) {
          statusEl.textContent = 'Workbook loaded. Search by rule number or keyword.';
        }
        return true;
      }
    
      function countChapterRules(chapter) {
        return chapter.sections.reduce((total, section) => total + (section.rules || []).length, 0);
      }
    
      function countTotalRules(chapters) {
        return chapters.reduce((total, chapter) => total + countChapterRules(chapter), 0);
      }
    
      function ensureSectionOpen(ruleEl) {
        const details = ruleEl ? ruleEl.closest('details') : null;
        if (details && !details.open) {
          details.open = true;
        }
      }
    
      function highlightRule(ruleEl, shouldScroll) {
        if (!ruleEl) return;
        document.querySelectorAll('.rule-line').forEach((node) => node.classList.remove('highlight'));
        ruleEl.classList.add('highlight');
        ensureSectionOpen(ruleEl);
        if (shouldScroll && ruleEl.id) {
          scrollToTargetId(ruleEl.id, { block: 'center' });
        }
      }
    
      function setActiveNavButton(activeBtn) {
        if (!navEl || !activeBtn) return;
        navEl.querySelectorAll('button').forEach((btn) => btn.classList.remove('is-active'));
        activeBtn.classList.add('is-active');
      }
    
      function setChapterExpanded(chapterBtn, expanded) {
        if (!chapterBtn) return;
        const group = chapterBtn.closest('.rules-nav-group');
        if (!group) return;
        const sectionWrap = group.querySelector('.rules-nav-sections');
        if (!sectionWrap) return;
        sectionWrap.hidden = !expanded;
        chapterBtn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        const chapterId = chapterBtn.dataset.chapterId;
        if (chapterId) {
          const stored = loadNavState();
          if (expanded) {
            stored[chapterId] = true;
          } else {
            delete stored[chapterId];
          }
          saveNavState(stored);
        }
      }
    
      function loadNavState() {
        if (!window.localStorage) return {};
        try {
          const raw = localStorage.getItem(navStateKey);
          return raw ? JSON.parse(raw) : {};
        } catch (err) {
          return {};
        }
      }
    
      function saveNavState(state) {
        if (!window.localStorage) return;
        try {
          localStorage.setItem(navStateKey, JSON.stringify(state || {}));
        } catch (err) {
          // ignore
        }
      }
    
      function applyNavState() {
        const stored = loadNavState();
        if (!navEl) return;
        navEl.querySelectorAll('.rules-nav-chapter').forEach((btn) => {
          const chapterId = btn.dataset.chapterId;
          if (!chapterId) return;
          const shouldExpand = Boolean(stored[chapterId]);
          const group = btn.closest('.rules-nav-group');
          const sectionWrap = group ? group.querySelector('.rules-nav-sections') : null;
          if (sectionWrap) {
            sectionWrap.hidden = !shouldExpand;
          }
          btn.setAttribute('aria-expanded', shouldExpand ? 'true' : 'false');
        });
      }
    
      function scrollToTargetId(targetId, options = {}) {
        if (!targetId) return false;
        const target = document.getElementById(targetId);
        if (!target) return false;
        const isScrollable = (node) => {
          if (!node) return false;
          const style = window.getComputedStyle(node);
          const canScrollY = /auto|scroll/.test(style.overflowY || '');
          return canScrollY && node.scrollHeight - node.clientHeight > 2;
        };
        const findScrollContainer = (node) => {
          const main = document.getElementById('main');
          if (main && main.contains(node) && isScrollable(main)) {
            return main;
          }
          let current = node && node.parentElement ? node.parentElement : null;
          while (current && current !== document.body && current !== document.documentElement) {
            if (isScrollable(current)) {
              return current;
            }
            current = current.parentElement;
          }
          return document.scrollingElement || document.documentElement;
        };
        const parentDetails = target.closest && target.closest('details');
        if (parentDetails && !parentDetails.open) {
          parentDetails.open = true;
        }
        if (target.tagName && target.tagName.toLowerCase() === 'details') {
          target.open = true;
        } else {
          const details = target.querySelector && target.querySelector('details');
          if (details && details.tagName.toLowerCase() === 'details') {
            details.open = true;
          }
        }
        const behavior = options.behavior || (prefersReducedMotion ? 'auto' : 'smooth');
        const block = options.block || 'start';
        const performScroll = () => {
          const scroller = findScrollContainer(target);
          if (scroller && scroller !== document.scrollingElement && scroller !== document.documentElement && scroller.contains(target)) {
            const scrollerRect = scroller.getBoundingClientRect();
            const targetRect = target.getBoundingClientRect();
            const offset = targetRect.top - scrollerRect.top + scroller.scrollTop;
            scroller.scrollTo({ top: Math.max(0, offset - 12), behavior });
          } else {
            target.scrollIntoView({ behavior, block });
          }
          if (target.focus) {
            target.focus({ preventScroll: true });
          }
        };
        requestAnimationFrame(performScroll);
        window.setTimeout(performScroll, 80);
        return true;
      }
    
      function navigateToTargetId(targetId, btn, options = {}) {
        const after = options.after;
        const go = () => {
          if (filterBtn && filterBtn.dataset.active === 'true') {
            filterBtn.dataset.active = 'false';
            filterBtn.classList.remove('is-active');
            filterBtn.textContent = 'Filter Workbook';
            clearFilter();
          }
          scrollToTargetId(targetId, options);
          if (btn) {
            setActiveNavButton(btn);
          }
          if (typeof after === 'function') {
            after();
          }
        };
        if (!workbookReady) {
          if (document.getElementById(targetId)) {
            go();
            return;
          }
          loadWorkbook().then(go);
          return;
        }
        go();
      }
    
      function resolveMatchId(match) {
        if (!match) return null;
        if (match.id) return match.id;
        const number = match.number ? String(match.number) : '';
        if (number && ruleMap.has(number)) {
          const rule = ruleMap.get(number);
          return rule && rule.id ? rule.id : null;
        }
        const text = match.text ? String(match.text) : '';
        const numberMatch = text.match(/^\s*(\d{3}\.\d+[a-z]?)/i);
        if (numberMatch && ruleMap.has(numberMatch[1])) {
          const rule = ruleMap.get(numberMatch[1]);
          return rule && rule.id ? rule.id : null;
        }
        return null;
      }
    
      function bindNavButtons() {
        if (!navEl || navEl.dataset.bound === 'true') return;
        navEl.dataset.bound = 'true';
        applyNavState();
        navEl.addEventListener('click', function (event) {
          const rawTarget = event.target;
          const elementTarget = rawTarget instanceof Element ? rawTarget : rawTarget && rawTarget.parentElement;
          const btn = elementTarget ? elementTarget.closest('button[data-target-id]') : null;
          if (!btn || !navEl.contains(btn)) return;
          const targetId = btn.dataset.targetId;
          if (btn.classList.contains('rules-nav-chapter')) {
            const expanded = btn.getAttribute('aria-expanded') === 'true';
            setChapterExpanded(btn, !expanded);
          } else {
            const parentId = btn.dataset.parentId;
            if (parentId) {
              const parentBtn = navEl.querySelector(`.rules-nav-chapter[data-chapter-id="${parentId}"]`);
              if (parentBtn) {
                setChapterExpanded(parentBtn, true);
              }
            }
          }
          navigateToTargetId(targetId, btn);
        });
      }
    
      function renderNav(chapters) {
        if (!navEl) return;
        navEl.innerHTML = '';
        chapters.forEach((chapter, idx) => {
          const group = document.createElement('div');
          group.className = 'rules-nav-group';
          group.dataset.chapterId = chapter.id;
    
          const chapterBtn = document.createElement('button');
          chapterBtn.type = 'button';
          chapterBtn.textContent = chapter.title;
          chapterBtn.dataset.targetId = chapter.id;
          chapterBtn.dataset.chapterId = chapter.id;
          chapterBtn.classList.add('rules-nav-chapter');
          if (idx === 0) chapterBtn.classList.add('is-active');
          chapterBtn.setAttribute('aria-expanded', 'false');
          chapterBtn.setAttribute('aria-controls', `rules-nav-${chapter.id}`);
    
          const sectionsWrap = document.createElement('div');
          sectionsWrap.className = 'rules-nav-sections';
          sectionsWrap.id = `rules-nav-${chapter.id}`;
          sectionsWrap.hidden = true;
    
          (chapter.sections || []).forEach((section) => {
            const sectionBtn = document.createElement('button');
            sectionBtn.type = 'button';
            sectionBtn.textContent = section.title;
            sectionBtn.dataset.targetId = section.id;
            sectionBtn.dataset.parentId = chapter.id;
            sectionBtn.classList.add('rules-nav-section');
            sectionsWrap.appendChild(sectionBtn);
          });
    
          group.appendChild(chapterBtn);
          group.appendChild(sectionsWrap);
          navEl.appendChild(group);
        });
        applyNavState();
        bindNavButtons();
      }
    
      function renderWorkbook(chapters) {
        if (!workbookEl) return;
        workbookEl.innerHTML = '';
        chapters.forEach((chapter) => {
          const card = document.createElement('section');
          card.className = 'rules-card';
          card.id = chapter.id;
    
          const heading = document.createElement('h2');
          heading.textContent = chapter.title;
          card.appendChild(heading);
    
          const meta = document.createElement('div');
          meta.className = 'rules-card-meta';
          const ruleCount = countChapterRules(chapter);
          meta.textContent = `${chapter.sections.length} section${chapter.sections.length === 1 ? '' : 's'} • ${ruleCount} rule${ruleCount === 1 ? '' : 's'}`;
          card.appendChild(meta);
    
          chapter.sections.forEach((section) => {
          const sectionEl = document.createElement('details');
          sectionEl.className = 'rules-section';
          sectionEl.id = section.id;
          sectionEl.open = false;
            const title = document.createElement('summary');
            title.className = 'rules-section-title';
            const titleText = document.createElement('span');
            titleText.textContent = section.title;
            const titleMeta = document.createElement('span');
            titleMeta.className = 'rules-section-meta';
            titleMeta.textContent = `${section.rules.length} rule${section.rules.length === 1 ? '' : 's'}`;
            title.appendChild(titleText);
            title.appendChild(titleMeta);
            sectionEl.appendChild(title);
    
            const body = document.createElement('div');
            body.className = 'rules-section-body';
            const hasNotes = section.notes && section.notes.length;
            const hasRules = section.rules && section.rules.length;
            if (!hasNotes || !hasRules) {
              body.classList.add('is-solo');
            }
    
            if (hasNotes) {
              const notesWrap = document.createElement('div');
              notesWrap.className = 'rules-section-notes';
              section.notes.forEach((note) => {
                const noteEl = document.createElement('div');
                noteEl.className = 'text-muted small mb-2';
                noteEl.textContent = note;
                notesWrap.appendChild(noteEl);
              });
              body.appendChild(notesWrap);
            }
    
            if (hasRules) {
              const rulesWrap = document.createElement('div');
              rulesWrap.className = 'rules-section-rules';
              section.rules.forEach((rule) => {
                const ruleEl = document.createElement('div');
                ruleEl.className = 'rule-line';
                ruleEl.id = rule.id;
                ruleEl.dataset.ruleNumber = rule.number;
                ruleEl.tabIndex = 0;
                const textEl = document.createElement('div');
                textEl.className = 'rule-text';
                textEl.textContent = rule.text;
                ruleEl.appendChild(textEl);
                if (rule.notes && rule.notes.length) {
                  const notesEl = document.createElement('div');
                  notesEl.className = 'rule-notes';
                  rule.notes.forEach((note) => {
                    const noteEl = document.createElement('div');
                    noteEl.className = 'rule-note';
                    noteEl.textContent = note;
                    notesEl.appendChild(noteEl);
                  });
                  ruleEl.appendChild(notesEl);
                }
                rulesWrap.appendChild(ruleEl);
              });
              body.appendChild(rulesWrap);
            }
    
            sectionEl.appendChild(body);
            card.appendChild(sectionEl);
          });
          workbookEl.appendChild(card);
        });
      }
    
      async function loadWorkbook(forceFetch) {
        if (workbookReady) {
          return;
        }
        if (!forceFetch && hydrateFromDOM()) {
          return;
        }
        if (workbookStatusEl) {
          workbookStatusEl.textContent = 'Loading rules workbook...';
        }
        try {
          let sourceLabel = '';
          if (hasInlineWorkbook) {
            rulesData = inlineWorkbookData;
            sourceLabel = 'inline workbook';
          } else {
            let text = '';
            const cached = window.localStorage ? localStorage.getItem(rulesStorageKey) : null;
            const hasCached = Boolean(cached && cached.length > 1000);
            if (!forceFetch && hasCached) {
              text = cached;
              sourceLabel = 'cache';
            } else {
              try {
                const workbookResp = await fetch(rulesWorkbookApiUrl, { credentials: 'same-origin' });
                if (workbookResp.ok) {
                  const payload = await workbookResp.json();
                  if (payload && payload.workbook) {
                    rulesData = payload.workbook;
                    sourceLabel = 'server workbook';
                  }
                }
              } catch (err) {
                // ignore
              }
              if (!rulesData.length) {
                try {
                  const apiResp = await fetch(rulesApiUrl, { credentials: 'same-origin' });
                  if (apiResp.ok) {
                    text = await apiResp.text();
                    sourceLabel = 'server';
                  }
                } catch (err) {
                  text = '';
                }
                if (!text) {
                  try {
                    const resp = await fetch(rulesUrl, { credentials: 'same-origin' });
                    if (resp.ok) {
                      text = await resp.text();
                      sourceLabel = 'static';
                    }
                  } catch (err) {
                    text = '';
                  }
                }
                if (window.localStorage && text) {
                  try {
                    localStorage.setItem(rulesStorageKey, text);
                    localStorage.setItem(rulesStorageMetaKey, String(Date.now()));
                  } catch (err) {
                    // ignore storage issues (quota, privacy mode)
                  }
                }
                rulesData = text ? parseRules(text) : [];
              }
            }
          }
    
          const totalRules = countTotalRules(rulesData);
          if (!rulesData.length || totalRules === 0) {
            if (workbookStatusEl) {
              workbookStatusEl.textContent = 'Rules could not be loaded. Please press Load Rules to try again.';
            }
            return;
          }
          ruleMap = buildRuleMap(rulesData);
          renderNav(rulesData);
          renderWorkbook(rulesData);
          workbookReady = true;
          if (loadBtn) {
            loadBtn.textContent = 'Load Rules';
          }
          if (workbookStatusEl) {
            const sourceNote = sourceLabel ? `Loaded from ${sourceLabel}.` : 'Loaded.';
            workbookStatusEl.textContent = `${sourceNote} ${totalRules} rules ready. Tip: click any rule to highlight it.`;
          }
          if (statusEl) {
            statusEl.textContent = 'Workbook loaded. Search by rule number or keyword.';
          }
        } catch (err) {
          console.error('Rules workbook error', err);
          if (workbookStatusEl) {
            workbookStatusEl.textContent = 'Unable to load the rules workbook.';
          }
        }
      }
    
      function refreshWorkbook() {
        if (workbookStatusEl) {
          workbookStatusEl.textContent = 'Refreshing rules workbook...';
        }
        if (window.localStorage) {
          localStorage.removeItem(rulesStorageKey);
          localStorage.removeItem(rulesStorageMetaKey);
        }
        rulesData = [];
        ruleMap = new Map();
        workbookReady = false;
        loadWorkbook(true);
      }
    
      function renderResults(matches) {
        if (!resultsEl) return;
        resultsEl.innerHTML = '';
        if (!matches || !matches.length) {
          const empty = document.createElement('div');
          empty.className = 'text-muted small';
          empty.textContent = 'No matches found.';
          resultsEl.appendChild(empty);
          return;
        }
        matches.forEach((match) => {
          const matchId = resolveMatchId(match);
          const wrap = document.createElement('div');
          wrap.className = 'rules-result';
          if (matchId) {
            wrap.dataset.matchId = matchId;
          }
          const line = document.createElement('small');
          if (match.number) {
            line.textContent = isRuleNumber(match.number)
              ? `Rule ${match.number}`
              : `Glossary — ${match.number}`;
          } else {
            line.textContent = 'Match';
          }
          const text = document.createElement('div');
          text.textContent = match.text || '';
          wrap.appendChild(line);
          wrap.appendChild(text);
          wrap.addEventListener('click', function () {
            if (!matchId) return;
            navigateToTargetId(matchId, null, {
              block: 'center',
              after: () => {
                const el = document.getElementById(matchId);
                if (el) {
                  highlightRule(el, false);
                }
              },
            });
          });
          resultsEl.appendChild(wrap);
        });
      }
    
      function localSearch(query) {
        const needle = query.toLowerCase();
        const results = [];
        rulesData.forEach((chapter) => {
          chapter.sections.forEach((section) => {
            section.rules.forEach((rule) => {
              const haystack = [rule.text].concat(rule.notes || []).join(' ').toLowerCase();
              if (haystack.includes(needle) || rule.number.includes(needle)) {
                results.push(rule);
              }
            });
          });
        });
        return results.slice(0, 25);
      }
    
      function isRuleNumber(value) {
        return /^\d{3}\./.test(String(value || '').trim());
      }
    
      async function searchRules() {
        const query = (searchInput && searchInput.value || '').trim();
        if (!query) {
          if (statusEl) statusEl.textContent = 'Load the rules, then search by rule number or keyword.';
          renderResults([]);
          lastQuery = '';
          lastMatches = [];
          if (filterBtn && filterBtn.dataset.active === 'true') {
            clearFilter();
          }
          return;
        }
        if (!workbookReady) {
          hydrateFromDOM();
          if (!workbookReady) {
            await loadWorkbook();
          }
        }
        if (statusEl) statusEl.textContent = 'Searching...';
        let matches = localSearch(query);
        if (!matches.length) {
          try {
            const resp = await fetch(`${rulesSearchApiUrl}?q=${encodeURIComponent(query)}&limit=25`, { credentials: 'same-origin' });
            if (resp.ok) {
              const payload = await resp.json();
              if (payload && Array.isArray(payload.matches)) {
                matches = payload.matches;
              }
            }
          } catch (err) {
            // ignore
          }
        }
        lastQuery = query;
        lastMatches = matches;
        renderResults(matches);
        if (filterBtn && filterBtn.dataset.active === 'true') {
          applyFilter(matches);
        }
        if (statusEl) {
          statusEl.textContent = matches.length
            ? `Showing ${matches.length} match${matches.length === 1 ? '' : 'es'}.`
            : 'No matches found.';
        }
        if (matches.length && (isRuleNumber(query) || matches.length === 1)) {
          const matchId = resolveMatchId(matches[0]);
          if (matchId) {
            navigateToTargetId(matchId, null, {
              block: 'center',
              after: () => {
                const el = document.getElementById(matchId);
                if (el) {
                  highlightRule(el, false);
                }
              },
            });
          }
        }
      }
    
      function clearFilter() {
        document.querySelectorAll('.rule-line').forEach((node) => node.classList.remove('is-hidden'));
        document.querySelectorAll('.rules-section').forEach((node) => node.classList.remove('is-hidden'));
        document.querySelectorAll('.rules-card').forEach((node) => node.classList.remove('is-hidden'));
      }
    
      function applyFilter(matches) {
        if (!matches || !matches.length) {
          clearFilter();
          if (workbookStatusEl) {
            workbookStatusEl.textContent = 'No matches to filter yet. Try a new search.';
          }
          return;
        }
        const matchIds = new Set(matches.map((match) => match.id));
        document.querySelectorAll('.rule-line').forEach((node) => {
          if (!matchIds.has(node.id)) {
            node.classList.add('is-hidden');
          } else {
            node.classList.remove('is-hidden');
          }
        });
        document.querySelectorAll('.rules-section').forEach((node) => {
          const hasVisible = node.querySelector('.rule-line:not(.is-hidden)');
          node.classList.toggle('is-hidden', !hasVisible);
          if (hasVisible && node.tagName.toLowerCase() === 'details') {
            node.open = true;
          }
        });
        document.querySelectorAll('.rules-card').forEach((node) => {
          const hasVisible = node.querySelector('.rules-section:not(.is-hidden)');
          node.classList.toggle('is-hidden', !hasVisible);
        });
        if (workbookStatusEl) {
          workbookStatusEl.textContent = `Filtered to ${matches.length} matching rule${matches.length === 1 ? '' : 's'}.`;
        }
      }
    
      function toggleFilter() {
        if (!filterBtn) return;
        const isActive = filterBtn.dataset.active === 'true';
        if (isActive) {
          filterBtn.dataset.active = 'false';
          filterBtn.classList.remove('is-active');
          filterBtn.textContent = 'Filter Workbook';
          clearFilter();
          if (workbookStatusEl) {
            workbookStatusEl.textContent = 'Tip: click any rule to highlight it.';
          }
          return;
        }
        filterBtn.dataset.active = 'true';
        filterBtn.classList.add('is-active');
        filterBtn.textContent = 'Show All';
        applyFilter(lastMatches);
      }
    
      function jumpToRule() {
        const query = (jumpInput && jumpInput.value || '').trim();
        if (!query) return;
        if (!workbookReady) {
          if (!hydrateFromDOM()) {
            loadWorkbook().then(() => jumpToRule());
            return;
          }
        }
        let rule = ruleMap.get(query);
        if (!rule) {
          for (const candidate of ruleMap.values()) {
            if (candidate.number && candidate.number.startsWith(query)) {
              rule = candidate;
              break;
            }
          }
        }
        if (rule && rule.id) {
          const el = document.getElementById(rule.id);
          if (el) {
            highlightRule(el, true);
          }
        } else if (statusEl) {
          statusEl.textContent = `No rule found for ${query}.`;
        }
      }
    
      function setAllSections(open) {
        document.querySelectorAll('.rules-section').forEach((section) => {
          if (section.tagName.toLowerCase() === 'details') {
            section.open = open;
          }
        });
      }
    
      if (loadBtn) {
        loadBtn.addEventListener('click', function () {
          refreshWorkbook();
        });
      }
      if (expandBtn) {
        expandBtn.addEventListener('click', function () {
          if (!workbookReady) {
            if (!hydrateFromDOM()) {
              loadWorkbook().then(() => setAllSections(true));
              return;
            }
          }
          setAllSections(true);
        });
      }
      if (collapseBtn) {
        collapseBtn.addEventListener('click', function () {
          if (!workbookReady) {
            if (!hydrateFromDOM()) {
              loadWorkbook().then(() => setAllSections(false));
              return;
            }
          }
          setAllSections(false);
        });
      }
      if (filterBtn) {
        filterBtn.addEventListener('click', function () {
          if (!workbookReady) {
            if (!hydrateFromDOM()) {
              loadWorkbook().then(() => toggleFilter());
              return;
            }
          }
          toggleFilter();
        });
      }
      if (jumpBtn) jumpBtn.addEventListener('click', jumpToRule);
      if (jumpInput) {
        jumpInput.addEventListener('keydown', function (event) {
          if (event.key === 'Enter') {
            event.preventDefault();
            jumpToRule();
          }
        });
      }
      if (searchBtn) searchBtn.addEventListener('click', searchRules);
      if (clearBtn) {
        clearBtn.addEventListener('click', function () {
          if (searchInput) searchInput.value = '';
          renderResults([]);
          if (statusEl) statusEl.textContent = 'Load the rules, then search by rule number or keyword.';
          document.querySelectorAll('.rule-line').forEach((node) => node.classList.remove('highlight'));
          if (filterBtn && filterBtn.dataset.active === 'true') {
            filterBtn.dataset.active = 'false';
            filterBtn.classList.remove('is-active');
            filterBtn.textContent = 'Filter Workbook';
            clearFilter();
          }
        });
      }
      if (searchInput) {
        searchInput.addEventListener('keydown', function (event) {
          if (event.key === 'Enter') {
            event.preventDefault();
            searchRules();
          }
        });
      }
      if (workbookEl) {
        workbookEl.addEventListener('click', function (event) {
          const target = event.target.closest('.rule-line');
          if (!target) return;
          highlightRule(target, false);
        });
        workbookEl.addEventListener('keydown', function (event) {
          if (event.key !== 'Enter' && event.key !== ' ') return;
          const target = event.target.closest('.rule-line');
          if (!target) return;
          event.preventDefault();
          highlightRule(target, false);
        });
      }
      bindNavButtons();
      loadWorkbook();
  }

  function boot() {
    initMagicRules();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
  document.addEventListener('htmx:afterSwap', boot);
  document.addEventListener('htmx:load', boot);
})();
