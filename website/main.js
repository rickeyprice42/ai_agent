const root = document.documentElement;
const themeButton = document.querySelector("#theme-toggle");
const menuButton = document.querySelector("#menu-toggle");
const menu = document.querySelector("#mobile-menu");
const dialog = document.querySelector("#status-dialog");
const platforms = document.querySelector("#platforms");
const platformNote = document.querySelector("#platform-note");
const dialogAction = document.querySelector("#dialog-action");
const config = window.AVELIN_SITE_CONFIG || { downloads: { fallbackPath: "./download.html", platforms: {} }, auth: { enabled: false, path: "./app/" } };
const translations = {
  ru: {"nav-capabilities":"Возможности","nav-memory":"Память","nav-privacy":"Приватность","hero-eyebrow":"Личный AI-агент","hero-title":"Помнит важное.<br>Помогает доводить дела до конца.","hero-copy":"Avelin объединяет диалоги, управляемую память, инструменты и выбор моделей в одном спокойном рабочем пространстве.",download:"Скачать",auth:"Войти / Зарегистрироваться",availability:"Desktop и web — скоро · мобильные приложения — в плане","section-work":"Рабочее пространство","section-work-title":"Один контекст для разговора и действий.","feature-dialogs":"Продолжай с того места, где остановился","feature-dialogs-copy":"История диалогов и проекты остаются рядом, когда задача растягивается на дни.","feature-files":"Получай готовый результат","feature-files-copy":"Документы и другие созданные файлы появляются в ответе как понятные карточки для скачивания.","feature-tools":"Подключай нужные инструменты","feature-tools-copy":"Avelin умеет работать с файлами, web-источниками и управляемыми действиями с подтверждением.","memory-eyebrow":"Управляемая память","memory-title":"Знает тебя лучше.<br>Оставляет контроль тебе.","memory-copy":"Avelin отделяет долговременные предпочтения от контекста конкретного чата. Записи можно просматривать, изменять, отзывать и удалять.",summary:"Сводка памяти","summary-status":"Обновляется с твоего согласия",preferences:"Предпочтения","preferences-copy":"Любит практичные решения и короткие понятные инструкции.",projects:"Проекты","projects-copy":"Развивает Avelin как личного агента и рабочую экосистему.","memory-control":"Просмотреть и изменить","models-eyebrow":"Модели","models-title":"Одна задача.<br>Несколько способов решить её.","models-copy":"Локальные и облачные модели можно выбирать под задачу. Резервные маршруты помогают сохранить работу при временной недоступности провайдера.","privacy-eyebrow":"Приватность","privacy-title":"Действия должны быть понятными.","privacy-approvals":"Подтверждения","privacy-approvals-copy":"Чувствительные и внешние действия не прячутся внутри ответа.","privacy-evidence":"Проверяемые результаты","privacy-evidence-copy":"Файлы, статусы и отчёты остаются доступными после выполнения.","privacy-local":"Локальный режим","privacy-local-copy":"Домашний preview по умолчанию остаётся доступен только на твоём компьютере.","closing-title":"Avelin становится полезнее вместе с тобой.","closing-copy":"Первая версия готовится к закрытому тестированию.","privacy-link":"Приватность","terms-link":"Условия",understand:"Закрыть"},
  en: {"nav-capabilities":"Capabilities","nav-memory":"Memory","nav-privacy":"Privacy","hero-eyebrow":"Personal AI agent","hero-title":"Remembers what matters.<br>Helps you finish the work.","hero-copy":"Avelin brings conversations, controlled memory, tools, and model choice into one calm workspace.",download:"Download",auth:"Sign in / Create account",availability:"Desktop and web — coming soon · mobile apps — planned","section-work":"Workspace","section-work-title":"One context for conversation and action.","feature-dialogs":"Continue where you left off","feature-dialogs-copy":"Conversation history and projects stay close when work stretches across days.","feature-files":"Get a finished result","feature-files-copy":"Documents and other generated files appear as clear, downloadable cards.","feature-tools":"Use the tools you need","feature-tools-copy":"Avelin works with files, web sources, and controlled actions that require approval.","memory-eyebrow":"Controlled memory","memory-title":"Knows you better.<br>Keeps you in control.","memory-copy":"Avelin separates long-term preferences from the context of a single chat. You can review, edit, revoke, and delete records.",summary:"Memory summary","summary-status":"Updated with your consent",preferences:"Preferences","preferences-copy":"Prefers practical solutions and short, clear instructions.",projects:"Projects","projects-copy":"Builds Avelin as a personal agent and working ecosystem.","memory-control":"Review and edit","models-eyebrow":"Models","models-title":"One task.<br>More than one way to solve it.","models-copy":"Choose local and cloud models for each task. Fallback routing helps preserve work when a provider is temporarily unavailable.","privacy-eyebrow":"Privacy","privacy-title":"Every action should be understandable.","privacy-approvals":"Approvals","privacy-approvals-copy":"Sensitive and external actions are never hidden inside an answer.","privacy-evidence":"Verifiable results","privacy-evidence-copy":"Files, statuses, and reports remain available after execution.","privacy-local":"Local mode","privacy-local-copy":"Your home preview remains available only on your computer by default.","closing-title":"Avelin becomes more useful with you.","closing-copy":"The first release is preparing for private testing.","privacy-link":"Privacy","terms-link":"Terms",understand:"Close"}
};
let language = "ru";

function applyTheme(theme) {
  root.dataset.theme = theme;
  localStorage.setItem("avelin-site-theme", theme);
  themeButton.setAttribute("aria-label", theme === "dark" ? "Включить светлую тему" : "Включить тёмную тему");
  document.querySelector('meta[name="theme-color"]').content = theme === "dark" ? "#050506" : "#f4f3ef";
}
applyTheme(localStorage.getItem("avelin-site-theme") || "dark");
themeButton.addEventListener("click", () => applyTheme(root.dataset.theme === "dark" ? "light" : "dark"));
document.querySelector("#language-toggle").addEventListener("click", event => {
  language = language === "ru" ? "en" : "ru";
  event.currentTarget.textContent = language === "ru" ? "EN" : "RU";
  root.lang = language;
  document.querySelectorAll("[data-copy]").forEach(element => {
    const text = translations[language][element.dataset.copy];
    if (text) element.innerHTML = text;
  });
});

function closeMenu() {
  menu.hidden = true;
  menuButton.setAttribute("aria-expanded", "false");
  menuButton.setAttribute("aria-label", "Открыть меню");
}
menuButton.addEventListener("click", () => {
  const opening = menu.hidden;
  menu.hidden = !opening;
  menuButton.setAttribute("aria-expanded", String(opening));
  menuButton.setAttribute("aria-label", opening ? "Закрыть меню" : "Открыть меню");
});
menu.querySelectorAll("a").forEach(link => link.addEventListener("click", closeMenu));
window.matchMedia("(min-width:761px)").addEventListener("change", event => { if (event.matches) closeMenu(); });

function detectPlatform() {
  const ua = navigator.userAgent || "";
  const platform = navigator.userAgentData?.platform || navigator.platform || "";
  if (/android/i.test(ua)) return "android";
  if (/iphone|ipad|ipod/i.test(ua) || (/mac/i.test(platform) && navigator.maxTouchPoints > 1)) return "ios";
  if (/mac/i.test(platform) || /macintosh/i.test(ua)) return "macos";
  return "windows";
}

function safeReturnPath(candidate = `${location.pathname}${location.search}${location.hash}`) {
  try {
    const parsed = new URL(candidate, location.origin);
    return parsed.origin === location.origin && parsed.pathname.startsWith("/") ? `${parsed.pathname}${parsed.search}${parsed.hash}` : "/";
  } catch {
    return "/";
  }
}

function safeConfiguredUrl(candidate, fallback, allowExternalHttps = false) {
  try {
    const parsed = new URL(candidate, location.href);
    if (parsed.origin === location.origin || (allowExternalHttps && parsed.protocol === "https:")) return parsed.href;
  } catch {
    // Invalid configuration falls through to the known local fallback.
  }
  return new URL(fallback, location.href).href;
}

function emitCtaEvent(name, platform, available) {
  window.dispatchEvent(new CustomEvent("avelin:cta", { detail: { name, platform: platform || null, available: Boolean(available) } }));
}

function platformCopy(platform) {
  return ({ windows: "Windows", macos: "macOS", ios: "iPhone / iPad", android: "Android" })[platform] || platform;
}

function selectPlatform(platform, detected = false, persist = true) {
  const route = config.downloads.platforms[platform] || { enabled: false, url: "" };
  if (persist) localStorage.setItem("avelin-platform", platform);
  const configuredTarget = route.enabled && route.url ? safeConfiguredUrl(route.url, config.downloads.fallbackPath, true) : "";
  const available = Boolean(configuredTarget && configuredTarget !== new URL(config.downloads.fallbackPath, location.href).href);
  platforms.querySelectorAll("button").forEach(button => {
    const selected = button.dataset.platform === platform;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
    button.querySelector("small").textContent = selected && detected ? (language === "ru" ? "Определено автоматически" : "Detected automatically") : "";
  });
  platformNote.hidden = false;
  platformNote.textContent = available
    ? (language === "ru" ? `Загрузка для ${platformCopy(platform)} доступна.` : `${platformCopy(platform)} download is available.`)
    : (language === "ru" ? `${platformCopy(platform)}: релиз ещё готовится. Битых ссылок на магазин не будет.` : `${platformCopy(platform)}: the release is still in progress. We will not send you to a broken store link.`);
  dialogAction.hidden = false;
  dialogAction.textContent = available ? (language === "ru" ? "Продолжить" : "Continue") : (language === "ru" ? "Страница загрузок" : "Downloads page");
  dialogAction.href = available ? configuredTarget : config.downloads.fallbackPath;
  emitCtaEvent("download_platform", platform, available);
}

function openDownload() {
  document.querySelector("#dialog-kicker").textContent = language === "ru" ? "Загрузка" : "Download";
  document.querySelector("#dialog-title").textContent = language === "ru" ? "Выбери платформу" : "Choose your platform";
  document.querySelector("#dialog-copy").textContent = language === "ru"
    ? "Мы предложили вариант по устройству, но выбор всегда можно изменить одним нажатием."
    : "We suggested an option for this device, but you can always change it with one click.";
  platforms.hidden = false;
  platformNote.hidden = true;
  dialogAction.hidden = true;
  const saved = localStorage.getItem("avelin-platform");
  const detected = detectPlatform();
  selectPlatform(saved || detected, !saved, Boolean(saved));
  dialog.showModal();
  emitCtaEvent("download_open", saved || detected, false);
}

function openAuth() {
  const authPath = config.auth.path || "./app/";
  if (config.auth.enabled) {
    const destination = new URL(safeConfiguredUrl(authPath, "./app/"));
    destination.searchParams.set("returnTo", safeReturnPath());
    emitCtaEvent("auth_open", null, true);
    location.assign(destination.href);
    return;
  }
  document.querySelector("#dialog-kicker").textContent = language === "ru" ? "Web-версия" : "Web app";
  document.querySelector("#dialog-title").textContent = language === "ru" ? "Вход скоро появится" : "Sign-in is coming soon";
  document.querySelector("#dialog-copy").textContent = language === "ru"
    ? "Закрытое тестирование web-версии ещё не началось. Эта кнопка не создаёт аккаунт и не ведёт на неготовую страницу."
    : "Private testing for the web app has not started. This button does not create an account or send you to an unfinished page.";
  platforms.hidden = true;
  platformNote.hidden = true;
  dialogAction.hidden = true;
  dialog.showModal();
  emitCtaEvent("auth_open", null, false);
}

document.querySelectorAll('[data-dialog="download"]').forEach(button => button.addEventListener("click", openDownload));
document.querySelectorAll('[data-dialog="auth"]').forEach(button => button.addEventListener("click", openAuth));
platforms.querySelectorAll("button").forEach(button => button.addEventListener("click", () => selectPlatform(button.dataset.platform)));
dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
window.AvelinSite = Object.freeze({ detectPlatform, safeReturnPath, safeConfiguredUrl });
