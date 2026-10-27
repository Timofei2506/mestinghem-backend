/* Mestinghem AI — landing page: ambient canvas, reveals, counters, forms === */
(function (global) {
  'use strict';
  const { t, MODULES, SECURITY, PRICING } = global.I18N;

  /* ---------------------------------------------------------- aurora canvas */
  function aurora() {
    const canvas = document.getElementById('aurora');
    if (!canvas) return;
    const ctx = canvas.getContext('2d', { alpha: true });
    if (!ctx) return;
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    let w = 0, h = 0, dpr = Math.min(global.devicePixelRatio || 1, 2);
    const blobs = [
      { x: .22, y: .28, r: .42, hue: [44, 62, 42], speed: .00013, phase: 0 },
      { x: .78, y: .34, r: .38, hue: [38, 55, 34], speed: .00019, phase: 2.1 },
      { x: .5, y: .82, r: .5, hue: [168, 42, 30], speed: .00011, phase: 4.2 },
      { x: .9, y: .8, r: .3, hue: [265, 40, 34], speed: .00023, phase: 1.1 },
    ];

    function resize() {
      w = canvas.clientWidth; h = canvas.clientHeight;
      canvas.width = Math.floor(w * dpr); canvas.height = Math.floor(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    addEventListener('resize', resize, { passive: true });

    let frame = 0;
    function draw(now) {
      frame = requestAnimationFrame(draw);
      ctx.clearRect(0, 0, w, h);
      ctx.globalCompositeOperation = 'lighter';
      for (const b of blobs) {
        const drift = reduce ? 0 : Math.sin(now * b.speed + b.phase);
        const cx = (b.x + drift * .05) * w;
        const cy = (b.y + Math.cos(now * b.speed * .8 + b.phase) * (reduce ? 0 : .04)) * h;
        const radius = b.r * Math.min(w, h) * (1 + (reduce ? 0 : drift * .07));
        const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
        grad.addColorStop(0, `hsla(${b.hue[0]}, ${b.hue[1]}%, ${b.hue[2]}%, .30)`);
        grad.addColorStop(.45, `hsla(${b.hue[0]}, ${b.hue[1]}%, ${b.hue[2]}%, .10)`);
        grad.addColorStop(1, 'hsla(0,0%,0%,0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalCompositeOperation = 'source-over';
    }
    frame = requestAnimationFrame(draw);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) cancelAnimationFrame(frame);
      else frame = requestAnimationFrame(draw);
    });
  }

  /* ---------------------------------------------------------------- reveals */
  function reveals() {
    const items = document.querySelectorAll('.reveal');
    if (!('IntersectionObserver' in global)) { items.forEach((el) => el.classList.add('in')); return; }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry, index) => {
        if (entry.isIntersecting) {
          setTimeout(() => entry.target.classList.add('in'), index * 70);
          io.unobserve(entry.target);
        }
      });
    }, { threshold: .12, rootMargin: '0px 0px -8% 0px' });
    items.forEach((el) => io.observe(el));
  }

  /* --------------------------------------------------------------- counters */
  function counters() {
    const nodes = document.querySelectorAll('[data-count]');
    if (!nodes.length) return;
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        io.unobserve(entry.target);
        const el = entry.target;
        const target = Number(el.dataset.count || 0);
        const duration = 1100;
        const started = performance.now();
        (function step(now) {
          const progress = Math.min(1, (now - started) / duration);
          const eased = 1 - Math.pow(1 - progress, 3);
          el.textContent = String(Math.round(target * eased));
          if (progress < 1) requestAnimationFrame(step);
        })(started);
      });
    }, { threshold: .5 });
    nodes.forEach((n) => io.observe(n));
  }

  /* ------------------------------------------------------- magnetic buttons */
  function magnetic() {
    if (matchMedia('(hover: none)').matches) return;
    document.querySelectorAll('.magnetic').forEach((el) => {
      el.addEventListener('mousemove', (ev) => {
        const box = el.getBoundingClientRect();
        const x = (ev.clientX - box.left - box.width / 2) / box.width;
        const y = (ev.clientY - box.top - box.height / 2) / box.height;
        el.style.transform = `translate(${x * 9}px, ${y * 7 - 2}px)`;
      });
      el.addEventListener('mouseleave', () => { el.style.transform = ''; });
    });
  }

  /* --------------------------------------------------------- card spotlight */
  function spotlight() {
    document.addEventListener('mousemove', (ev) => {
      const card = ev.target.closest && ev.target.closest('.module-card');
      if (!card) return;
      const box = card.getBoundingClientRect();
      card.style.setProperty('--mx', `${ev.clientX - box.left}px`);
      card.style.setProperty('--my', `${ev.clientY - box.top}px`);
    }, { passive: true });
  }

  /* --------------------------------------------------------------- 3D tilt */
  function tilt() {
    const card = document.querySelector('.tilt');
    if (!card || matchMedia('(hover: none)').matches) return;
    const host = card.parentElement;
    host.addEventListener('mousemove', (ev) => {
      const box = host.getBoundingClientRect();
      const x = (ev.clientX - box.left) / box.width - .5;
      const y = (ev.clientY - box.top) / box.height - .5;
      card.style.transform = `perspective(1100px) rotateY(${x * 9}deg) rotateX(${-y * 9}deg) translateZ(0)`;
    });
    host.addEventListener('mouseleave', () => { card.style.transform = ''; });
  }

  /* --------------------------------------------------------- typing demo */
  function demo() {
    const user = document.querySelector('[data-type="analyze"]');
    const out = document.querySelector('[data-type="stream"]');
    if (!user || !out) return;
    const lines = {
      ru: [
        'проанализируй рынок и собери презентацию',
        'сделай финансовую модель на 12 месяцев',
        'разбери мою партию и укажи на ошибку',
        'подготовь отчёт по рискам в Excel',
      ],
      en: [
        'analyse the market and build a deck',
        'make a 12-month financial model',
        'review my game and find the mistake',
        'prepare a risk report in Excel',
      ],
    };
    const answers = {
      ru: 'Принято. Строю структуру: рынок → сегменты → экономика → риски → план. Презентация и модель появятся в вашей директории через минуту.',
      en: 'Understood. Building the structure: market → segments → economics → risks → plan. The deck and model will appear in your directory shortly.',
    };
    let index = 0;
    async function cycle() {
      const lang = global.I18N.lang;
      const text = lines[lang][index % lines[lang].length];
      user.textContent = '';
      out.textContent = '';
      for (let i = 0; i < text.length; i++) {
        user.textContent += text[i];
        await sleep(26 + Math.random() * 34);
      }
      await sleep(520);
      const answer = answers[lang];
      for (let i = 0; i < answer.length; i++) {
        out.textContent += answer[i];
        await sleep(11 + Math.random() * 14);
      }
      await sleep(2600);
      index++;
      cycle();
    }
    cycle();
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  /* ------------------------------------------------------------- sections */
  function renderModules() {
    const host = document.getElementById('moduleGrid');
    if (!host) return;
    host.innerHTML = MODULES[global.I18N.lang].map((m, i) => `
      <article class="module-card reveal" data-module="${m.id}" style="transition-delay:${i * 45}ms">
        <div class="module-ico">${ico(m.icon)}</div>
        <h3>${m.title}</h3>
        <p>${m.text}</p>
        <div class="module-tags">${m.tags.map((tag) => `<span class="tag">${tag}</span>`).join('')}</div>
      </article>`).join('');
    host.querySelectorAll('.module-card').forEach((card) => {
      card.addEventListener('click', () => {
        const id = card.dataset.module;
        if (global.API && global.API.isAuthenticated) location.hash = `#/app/${id}`;
        else global.App.openAuth('register');
      });
    });
    reveals();
  }

  function renderSecurity() {
    const host = document.getElementById('securityGrid');
    if (!host) return;
    host.innerHTML = SECURITY[global.I18N.lang].map((s, i) => `
      <div class="sec-item reveal" style="transition-delay:${i * 35}ms">
        <h4><span>${ico(s.icon)}</span>${s.title}</h4><p>${s.text}</p>
      </div>`).join('');
    reveals();
  }

  function renderPricing() {
    const host = document.getElementById('priceGrid');
    if (!host) return;
    host.innerHTML = PRICING[global.I18N.lang].map((p, i) => `
      <div class="price-card reveal ${p.featured ? 'featured' : ''}" ${p.badge ? `data-badge="${p.badge}"` : ''} style="transition-delay:${i * 60}ms">
        <div class="price-name">${p.name}</div>
        <div class="price-value">${/^\d+$/.test(p.price) ? `€${p.price}` : p.price} ${p.period ? `<small>${p.period}</small>` : ''}</div>
        <div class="price-desc">${p.desc}</div>
        <ul class="price-list">${p.features.map((f) => `<li>${f}</li>`).join('')}</ul>
        <button class="btn ${p.featured ? 'btn-gold' : 'btn-outline'} btn-block" data-plan="${p.id}">${p.cta}</button>
      </div>`).join('');
    host.querySelectorAll('[data-plan]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.dataset.plan === 'enterprise') { location.href = 'mailto:support@mestinghem.ai'; return; }
        global.App.openAuth('register');
      });
    });
    reveals();
  }

  /* ------------------------------------------------------------------ auth */
  function initAuth() {
    const modal = document.getElementById('authModal');
    const loginForm = document.getElementById('loginForm');
    const registerForm = document.getElementById('registerForm');
    if (!modal || !loginForm || !registerForm) return;

    document.querySelectorAll('#authModal [data-close]').forEach((el) =>
      el.addEventListener('click', () => global.App.closeAuth()));
    document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && !modal.hidden) global.App.closeAuth(); });

    modal.querySelectorAll('.auth-tab').forEach((tab) => {
      tab.addEventListener('click', () => global.App.switchAuthTab(tab.dataset.tab));
    });

    modal.querySelectorAll('[data-toggle-pw]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const input = btn.parentElement.querySelector('input');
        input.type = input.type === 'password' ? 'text' : 'password';
      });
    });

    const pw = registerForm.querySelector('input[name="password"]');
    const bar = document.getElementById('pwBar');
    const hint = document.getElementById('pwHint');
    pw.addEventListener('input', () => {
      const value = pw.value;
      const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^\w\s]/].filter((re) => re.test(value)).length;
      let score = 0;
      if (value.length >= 10) score++;
      if (value.length >= 16) score++;
      if (classes >= 3) score++;
      if (classes === 4 && value.length >= 16) score++;
      const width = Math.min(100, score * 26);
      bar.style.width = `${width}%`;
      bar.style.background = ['#d96a7a', '#e08a52', '#e0b252', '#7fdcb8', '#4fc3a1'][Math.max(0, Math.min(4, score))];
      hint.textContent = score >= 3
        ? (global.I18N.lang === 'ru' ? 'Надёжный пароль' : 'Strong password')
        : t('auth.pwHint');
    });

    loginForm.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const error = document.getElementById('loginError');
      const button = loginForm.querySelector('button[type="submit"]');
      error.hidden = true;
      button.classList.add('loading');
      const data = Object.fromEntries(new FormData(loginForm));
      try {
        await global.API.auth.login({
          identifier: data.identifier,
          password: data.password,
          mfa_code: data.mfa_code || null,
          remember: Boolean(loginForm.querySelector('[name="remember"]').checked),
        });
        global.App.closeAuth();
        global.App.toast('ok', global.I18N.lang === 'ru' ? 'Добро пожаловать' : 'Welcome back', data.identifier);
        location.hash = '#/app/chat';
        global.App.enterWorkspace();
      } catch (e) {
        error.textContent = e.message || t('common.error');
        error.hidden = false;
        if (/2FA|двухфактор/i.test(e.message || '')) document.getElementById('mfaField').hidden = false;
      } finally {
        button.classList.remove('loading');
      }
    });

    registerForm.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const error = document.getElementById('registerError');
      const button = registerForm.querySelector('button[type="submit"]');
      error.hidden = true;
      button.classList.add('loading');
      const form = new FormData(registerForm);
      const payload = {
        email: form.get('email'),
        username: form.get('username'),
        password: form.get('password'),
        display_name: form.get('display_name') || null,
        language: global.I18N.lang,
        accept_terms: Boolean(form.get('accept_terms')),
        accept_privacy: Boolean(form.get('accept_privacy')),
        consent_marketing: Boolean(form.get('consent_marketing')),
      };
      try {
        await global.API.auth.register(payload);
        global.App.closeAuth();
        global.App.toast('ok', global.I18N.lang === 'ru' ? 'Аккаунт создан' : 'Account created',
          global.I18N.lang === 'ru' ? 'Рабочая область готова' : 'Your workspace is ready');
        location.hash = '#/app/chat';
        global.App.enterWorkspace();
      } catch (e) {
        error.textContent = e.message || t('common.error');
        error.hidden = false;
      } finally {
        button.classList.remove('loading');
      }
    });
  }

  /* ---------------------------------------------------------------- status */
  async function statusPill() {
    const pill = document.getElementById('statusPill');
    if (!pill) return;
    const label = pill.querySelector('span:last-child');
    try {
      const health = await global.API.system.health();
      const ok = health.status === 'ok';
      pill.querySelector('.pulse').style.background = ok ? 'var(--ok)' : 'var(--warn)';
      label.textContent = ok
        ? (global.I18N.lang === 'ru' ? 'все системы в норме' : 'all systems nominal')
        : (global.I18N.lang === 'ru' ? 'деградация сервиса' : 'degraded');
    } catch (e) {
      label.textContent = global.I18N.lang === 'ru' ? 'нет связи' : 'unreachable';
      pill.querySelector('.pulse').style.background = 'var(--err)';
    }
  }

  function initNav() {
    ['btnLogin', 'btnStart', 'heroStart', 'ctaStart'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('click', () => global.App.openAuth(id === 'btnLogin' ? 'login' : 'register'));
    });
    const tour = document.getElementById('heroTour');
    if (tour) tour.addEventListener('click', () => document.getElementById('platform').scrollIntoView({ behavior: 'smooth' }));
    const toggle = document.getElementById('langToggle');
    if (toggle) toggle.addEventListener('click', () => global.I18N.setLang(global.I18N.lang === 'ru' ? 'en' : 'ru'));
  }

  global.Landing = {
    init() {
      aurora();
      initNav();
      initAuth();
      renderModules();
      renderSecurity();
      renderPricing();
      reveals();
      counters();
      magnetic();
      spotlight();
      tilt();
      demo();
      statusPill();
    },
    render() {
      renderModules();
      renderSecurity();
      renderPricing();
      statusPill();
      magnetic();
    },
  };
})(window);
