/* Mestinghem AI — i18n (RU / EN) ========================================= */
(function (global) {
  'use strict';

  const DICT = {
    ru: {
      boot: 'Инициализация защищённой среды…',
      'nav.platform': 'Платформа', 'nav.chess': 'Шахматы', 'nav.security': 'Безопасность',
      'nav.pricing': 'Тарифы', 'nav.legal': 'Документы', 'nav.login': 'Войти', 'nav.start': 'Начать работу',
      'hero.eyebrow': 'Автономная рабочая платформа · v1.0',
      'hero.t1': 'Думает.', 'hero.t2': 'Строит.', 'hero.t3': 'Решает.',
      'hero.sub': 'Один интерфейс вместо десятка инструментов: аналитика, документы, презентации, таблицы, генерация изображений, терминал, шахматный анализ и управление вашей рабочей станцией — под полным контролем и с полным журналом действий.',
      'hero.cta1': 'Создать аккаунт', 'hero.cta2': 'Смотреть платформу',
      'hero.m1': 'рабочих модулей', 'hero.m2': 'языка интерфейса', 'hero.m3': 'таблиц аудита', 'hero.m4': 'журналирование',
      'hero.a1': 'Презентация · 12 слайдов', 'hero.a2': 'Модель · 4 листа', 'hero.a3': 'Визуал · 4 кадра',
      'hero.c1': 'Терминал в песочнице', 'hero.c2': 'Шахматный анализ', 'hero.c3': 'Агент на вашем ПК',
      'modules.kicker': 'Модули', 'modules.title': 'Шесть инструментов, одна среда',
      'modules.sub': 'Каждый модуль работает самостоятельно и усиливает остальные: результат анализа сразу становится документом, документ — файлом в вашей директории.',
      'sec.kicker': 'Безопасность', 'sec.title': 'Контроль по умолчанию',
      'sec.sub': 'Принцип deny-by-default: ничего не выполняется без вашего явного согласия, а всё выполненное — записано.',
      'price.kicker': 'Тарифы', 'price.title': 'Прозрачные уровни доступа',
      'cta.title': 'Готовы передать рутину платформе?',
      'cta.sub': 'Аккаунт создаётся за 30 секунд. Данные — в вашей рабочей области, экспорт и удаление доступны в один клик.',
      'cta.btn': 'Начать бесплатно',
      'footer.about': 'Автономная рабочая платформа для тех, кто принимает решения.',
      'footer.product': 'Продукт', 'footer.legal': 'Документы', 'footer.contact': 'Контакты',
      'footer.privacy': 'Политика конфиденциальности', 'footer.terms': 'Условия использования',
      'footer.security': 'Безопасность', 'footer.agent': 'Политика управления устройством',
      'footer.cookies': 'Cookie', 'footer.aup': 'Допустимое использование', 'footer.dpa': 'DPA',
      'footer.disclosure': 'Раскрытие уязвимостей', 'footer.rights': 'Все права защищены.',
      'footer.status': 'все системы в норме', 'footer.dpo': 'DPO: dpo@mestinghem.ai',
      'auth.login': 'Вход', 'auth.register': 'Регистрация', 'auth.loginTitle': 'С возвращением',
      'auth.loginSub': 'Войдите, чтобы продолжить работу в защищённой среде.',
      'auth.regTitle': 'Создать аккаунт',
      'auth.regSub': 'Рабочая область, директория и все модули — сразу после регистрации.',
      'auth.email': 'E-mail или логин', 'auth.password': 'Пароль', 'auth.mfa': 'Код 2FA',
      'auth.remember': 'Запомнить устройство', 'auth.submit': 'Войти', 'auth.create': 'Создать аккаунт',
      'auth.name': 'Имя', 'auth.username': 'Логин',
      'auth.acceptTerms': 'Принимаю', 'auth.acceptPrivacy': 'Принимаю',
      'auth.consentMarketing': 'Согласен получать сообщения о продукте (можно отключить в любой момент)',
      'auth.pwHint': 'Минимум 10 символов, смешайте регистр, цифры и знаки.',
      'pal.nav': 'навигация', 'pal.run': 'выполнить', 'pal.close': 'закрыть',

      /* workspace */
      'rail.chat': 'Ассистент', 'rail.directory': 'Директория', 'rail.terminal': 'Терминал',
      'rail.documents': 'Документы', 'rail.images': 'Изображения', 'rail.chess': 'Шахматы',
      'rail.agent': 'Рабочая станция', 'rail.security': 'Безопасность', 'rail.settings': 'Настройки',
      'rail.logout': 'Выйти',

      'chat.title': 'Ассистент', 'chat.sub': 'Задайте задачу — платформа соберёт ответ, документы и файлы в вашей директории.',
      'chat.placeholder': 'Опишите задачу…  Enter — отправить, Shift+Enter — новая строка',
      'chat.new': 'Новый диалог', 'chat.empty.title': 'С чего начнём?',
      'chat.empty.sub': 'Несколько идей, чтобы показать возможности платформы.',
      'chat.attach': 'Прикрепить файл', 'chat.stream': 'Потоковый ответ', 'chat.clear': 'Очистить',
      'chat.you': 'Вы', 'chat.ai': 'Mestinghem', 'chat.actions': 'Действия платформы',
      'chat.thinking': 'Обрабатываю запрос', 'chat.docMade': 'Документ собран',
      'chat.imgMade': 'Изображение готово', 'chat.fileMade': 'Файл сохранён',
      'chat.download': 'Скачать', 'chat.open': 'Открыть', 'chat.tokens': 'токенов', 'chat.ms': 'мс',

      'dir.title': 'Директория', 'dir.sub': 'Ваша рабочая область: документы, таблицы, презентации, изображения и код.',
      'dir.upload': 'Загрузить', 'dir.newFolder': 'Новая папка', 'dir.newFile': 'Новый файл',
      'dir.search': 'Поиск по имени и пути…', 'dir.all': 'Все файлы', 'dir.trash': 'Корзина',
      'dir.grid': 'Плитка', 'dir.list': 'Список', 'dir.empty': 'Пока пусто. Загрузите файл или создайте документ.',
      'dir.name': 'Имя', 'dir.kind': 'Тип', 'dir.size': 'Размер', 'dir.modified': 'Изменён',
      'dir.download': 'Скачать', 'dir.rename': 'Переименовать', 'dir.move': 'Переместить',
      'dir.pin': 'Закрепить', 'dir.delete': 'Удалить', 'dir.drop': 'Перетащите файлы сюда',
      'dir.storage': 'Занято', 'dir.files': 'файлов',

      'term.title': 'Терминал', 'term.sub': 'Изолированная песочница: команды выполняются в вашей рабочей области, деструктивные операции блокируются политикой.',
      'term.placeholder': 'Команда…', 'term.run': 'Выполнить', 'term.new': 'Новая сессия',
      'term.close': 'Закрыть сессию', 'term.policy': 'Политика', 'term.history': 'История',
      'term.quick': 'Быстрые команды', 'term.blocked': 'Команда заблокирована',
      'term.off': 'Терминал отключён. Включите его в разделе «Настройки → Возможности».',
      'term.enabled': 'Терминал включён', 'term.sandbox': 'Песочница', 'term.timeout': 'Таймаут',

      'docs.title': 'Документы', 'docs.sub': 'Презентации, таблицы Excel, документы Word, Markdown и HTML — по вашему ТЗ, в фирменном стиле.',
      'docs.kind': 'Формат', 'docs.brief': 'Задание', 'docs.briefPh': 'Опишите тему, цель, аудиторию, ключевые цифры и структуру…',
      'docs.size': 'Объём', 'docs.audience': 'Аудитория', 'docs.tone': 'Тон', 'docs.build': 'Собрать документ',
      'docs.short': 'Кратко', 'docs.medium': 'Стандарт', 'docs.long': 'Подробно',
      'docs.mine': 'Мои документы', 'docs.none': 'Документов пока нет', 'docs.ready': 'Документ готов',
      'docs.preview': 'Предпросмотр', 'docs.rebuild': 'Пересобрать', 'docs.working': 'Собираю документ…',
      'docs.slides': 'слайдов', 'docs.rows': 'строк', 'docs.sheets': 'листов', 'docs.sections': 'разделов',

      'img.title': 'Изображения', 'img.sub': 'Генерация визуалов в фирменной эстетике: от фотореализма до люксового эдиториала.',
      'img.prompt': 'Описание', 'img.promptPh': 'Что изобразить: объект, сцена, свет, настроение, детали…',
      'img.negative': 'Исключить', 'img.style': 'Стиль', 'img.size': 'Размер', 'img.count': 'Количество',
      'img.generate': 'Сгенерировать', 'img.gallery': 'Галерея', 'img.working': 'Генерирую…',
      'img.done': 'Изображение готово', 'img.empty': 'Галерея пуста',

      'chess.title': 'Шахматы', 'chess.sub': 'Партия с платформой, анализ позиции и гроссмейстерский комментарий к каждому ходу.',
      'chess.new': 'Новая партия', 'chess.side': 'Ваш цвет', 'chess.white': 'Белые', 'chess.black': 'Чёрные',
      'chess.random': 'Случайно', 'chess.level': 'Уровень', 'chess.mode': 'Режим',
      'chess.modeEngine': 'Партия с платформой', 'chess.modeAnalysis': 'Анализ позиции', 'chess.modeHotseat': 'Двое за доской',
      'chess.start': 'Начать партию', 'chess.hint': 'Подсказка', 'chess.analyse': 'Анализ позиции',
      'chess.review': 'Разбор партии', 'chess.undo': 'Отменить ход', 'chess.resign': 'Сдаться',
      'chess.pgn': 'Экспорт PGN', 'chess.moves': 'Ходы', 'chess.eval': 'Оценка', 'chess.plans': 'Планы',
      'chess.opening': 'Дебют', 'chess.yourTurn': 'Ваш ход', 'chess.thinking': 'Платформа думает…',
      'chess.over': 'Партия завершена', 'chess.win': 'Победа', 'chess.lose': 'Поражение', 'chess.draw': 'Ничья',
      'chess.accuracy': 'Точность', 'chess.commentary': 'Комментарий', 'chess.noGames': 'Партий пока нет',
      'chess.flip': 'Перевернуть доску', 'chess.material': 'Материал', 'chess.captured': 'Взятые фигуры',

      'agent.title': 'Рабочая станция', 'agent.sub': 'Подключите свой компьютер, чтобы платформа помогала с реальными задачами — под вашим полным контролем.',
      'agent.off': 'Модуль отключён. Включите его в разделе «Настройки → Возможности» — это требует явного согласия.',
      'agent.enable': 'Включить модуль', 'agent.pair': 'Подключить устройство',
      'agent.devices': 'Устройства', 'agent.approvals': 'Ожидают подтверждения', 'agent.history': 'История действий',
      'agent.profile': 'Профиль прав', 'agent.approve': 'Одобрить', 'agent.deny': 'Отклонить',
      'agent.revoke': 'Отозвать доступ', 'agent.download': 'Скачать агент',
      'agent.consent': 'Я понимаю, что платформа сможет выполнять одобренные мной действия на этом устройстве, и принимаю Политику управления устройством.',
      'agent.trusted': 'Доверенные папки', 'agent.requireApproval': 'Требовать подтверждение каждого действия',
      'agent.noDevices': 'Устройства не подключены', 'agent.code': 'Код сопряжения',
      'agent.deviceKey': 'Ключ устройства', 'agent.guarantees': 'Гарантии безопасности',
      'agent.forbidden': 'Запрещено всегда', 'agent.action': 'Действие', 'agent.ask': 'Запросить действие',

      'sec.title': 'Безопасность', 'sec.sub': 'Оценка защиты аккаунта, журнал событий и активные сессии.',
      'sec.score': 'Индекс защиты', 'sec.events': 'События безопасности', 'sec.sessions': 'Сессии',
      'sec.audit': 'Журнал действий', 'sec.revokeAll': 'Завершить все сессии', 'sec.mfa': 'Двухфакторная аутентификация',
      'sec.export': 'Выгрузить мои данные', 'sec.erase': 'Удалить аккаунт',

      'set.title': 'Настройки', 'set.profile': 'Профиль', 'set.appearance': 'Внешний вид',
      'set.capabilities': 'Возможности', 'set.keys': 'API-ключи', 'set.about': 'О платформе',
      'set.language': 'Язык интерфейса', 'set.theme': 'Тема', 'set.animations': 'Анимации',
      'set.save': 'Сохранить', 'set.saved': 'Сохранено', 'set.displayName': 'Отображаемое имя',
      'set.cap.chat': 'Ассистент и диалоги', 'set.cap.files': 'Директория и файлы',
      'set.cap.documents': 'Генерация документов', 'set.cap.images': 'Генерация изображений',
      'set.cap.chess': 'Шахматный модуль', 'set.cap.terminal': 'Терминал в песочнице',
      'set.cap.terminal_packages': 'Установка пакетов в терминале', 'set.cap.agent': 'Агент на рабочей станции',
      'set.newKey': 'Создать ключ', 'set.keyName': 'Название ключа', 'set.keyWarn': 'Ключ показывается один раз — скопируйте его сейчас.',

      'common.loading': 'Загрузка…', 'common.error': 'Ошибка', 'common.cancel': 'Отмена',
      'common.close': 'Закрыть', 'common.copy': 'Копировать', 'common.copied': 'Скопировано',
      'common.confirm': 'Подтвердить', 'common.delete': 'Удалить', 'common.save': 'Сохранить',
      'common.open': 'Открыть', 'common.download': 'Скачать', 'common.refresh': 'Обновить',
      'common.none': 'нет', 'common.yes': 'да', 'common.no': 'нет', 'common.back': 'Назад',
      'common.sessionExpired': 'Сессия истекла, войдите снова',
      'common.networkError': 'Нет связи с платформой',
    },

    en: {
      boot: 'Initialising secure environment…',
      'nav.platform': 'Platform', 'nav.chess': 'Chess', 'nav.security': 'Security',
      'nav.pricing': 'Pricing', 'nav.legal': 'Legal', 'nav.login': 'Sign in', 'nav.start': 'Get started',
      'hero.eyebrow': 'Autonomous work platform · v1.0',
      'hero.t1': 'Thinks.', 'hero.t2': 'Builds.', 'hero.t3': 'Decides.',
      'hero.sub': 'One interface instead of a dozen tools: analytics, documents, presentations, spreadsheets, image generation, terminal, chess analysis and control of your workstation — fully supervised and fully logged.',
      'hero.cta1': 'Create account', 'hero.cta2': 'Explore the platform',
      'hero.m1': 'working modules', 'hero.m2': 'interface languages', 'hero.m3': 'audit tables', 'hero.m4': 'logging',
      'hero.a1': 'Deck · 12 slides', 'hero.a2': 'Model · 4 sheets', 'hero.a3': 'Visuals · 4 frames',
      'hero.c1': 'Sandboxed terminal', 'hero.c2': 'Chess analysis', 'hero.c3': 'Agent on your PC',
      'modules.kicker': 'Modules', 'modules.title': 'Six tools, one environment',
      'modules.sub': 'Every module stands alone and amplifies the others: an analysis becomes a document, a document becomes a file in your directory.',
      'sec.kicker': 'Security', 'sec.title': 'Control by default',
      'sec.sub': 'Deny-by-default: nothing runs without your explicit consent, and everything that runs is recorded.',
      'price.kicker': 'Pricing', 'price.title': 'Transparent access tiers',
      'cta.title': 'Ready to hand the routine to the platform?',
      'cta.sub': 'An account takes 30 seconds. Your data stays in your workspace; export and erasure are one click away.',
      'cta.btn': 'Start for free',
      'footer.about': 'An autonomous work platform for people who make decisions.',
      'footer.product': 'Product', 'footer.legal': 'Legal', 'footer.contact': 'Contacts',
      'footer.privacy': 'Privacy Policy', 'footer.terms': 'Terms of Service',
      'footer.security': 'Security', 'footer.agent': 'Device Control Policy',
      'footer.cookies': 'Cookies', 'footer.aup': 'Acceptable Use', 'footer.dpa': 'DPA',
      'footer.disclosure': 'Vulnerability disclosure', 'footer.rights': 'All rights reserved.',
      'footer.status': 'all systems nominal', 'footer.dpo': 'DPO: dpo@mestinghem.ai',
      'auth.login': 'Sign in', 'auth.register': 'Sign up', 'auth.loginTitle': 'Welcome back',
      'auth.loginSub': 'Sign in to continue inside the secure environment.',
      'auth.regTitle': 'Create account',
      'auth.regSub': 'Workspace, directory and every module — right after sign-up.',
      'auth.email': 'E-mail or username', 'auth.password': 'Password', 'auth.mfa': '2FA code',
      'auth.remember': 'Remember this device', 'auth.submit': 'Sign in', 'auth.create': 'Create account',
      'auth.name': 'Name', 'auth.username': 'Username',
      'auth.acceptTerms': 'I accept the', 'auth.acceptPrivacy': 'I accept the',
      'auth.consentMarketing': 'I agree to receive product updates (can be turned off at any time)',
      'auth.pwHint': 'At least 10 characters; mix cases, digits and symbols.',
      'pal.nav': 'navigate', 'pal.run': 'run', 'pal.close': 'close',

      'rail.chat': 'Assistant', 'rail.directory': 'Directory', 'rail.terminal': 'Terminal',
      'rail.documents': 'Documents', 'rail.images': 'Images', 'rail.chess': 'Chess',
      'rail.agent': 'Workstation', 'rail.security': 'Security', 'rail.settings': 'Settings',
      'rail.logout': 'Sign out',

      'chat.title': 'Assistant', 'chat.sub': 'Give the task — the platform answers and produces documents and files in your directory.',
      'chat.placeholder': 'Describe the task…  Enter to send, Shift+Enter for a new line',
      'chat.new': 'New chat', 'chat.empty.title': 'Where shall we start?',
      'chat.empty.sub': 'A few ideas to show what the platform can do.',
      'chat.attach': 'Attach file', 'chat.stream': 'Streaming', 'chat.clear': 'Clear',
      'chat.you': 'You', 'chat.ai': 'Mestinghem', 'chat.actions': 'Platform actions',
      'chat.thinking': 'Working on it', 'chat.docMade': 'Document ready',
      'chat.imgMade': 'Image ready', 'chat.fileMade': 'File saved',
      'chat.download': 'Download', 'chat.open': 'Open', 'chat.tokens': 'tokens', 'chat.ms': 'ms',

      'dir.title': 'Directory', 'dir.sub': 'Your workspace: documents, spreadsheets, decks, images and code.',
      'dir.upload': 'Upload', 'dir.newFolder': 'New folder', 'dir.newFile': 'New file',
      'dir.search': 'Search by name or path…', 'dir.all': 'All files', 'dir.trash': 'Trash',
      'dir.grid': 'Grid', 'dir.list': 'List', 'dir.empty': 'Nothing here yet. Upload a file or build a document.',
      'dir.name': 'Name', 'dir.kind': 'Type', 'dir.size': 'Size', 'dir.modified': 'Modified',
      'dir.download': 'Download', 'dir.rename': 'Rename', 'dir.move': 'Move',
      'dir.pin': 'Pin', 'dir.delete': 'Delete', 'dir.drop': 'Drop files here',
      'dir.storage': 'Used', 'dir.files': 'files',

      'term.title': 'Terminal', 'term.sub': 'An isolated sandbox: commands run inside your workspace, destructive operations are blocked by policy.',
      'term.placeholder': 'Command…', 'term.run': 'Run', 'term.new': 'New session',
      'term.close': 'Close session', 'term.policy': 'Policy', 'term.history': 'History',
      'term.quick': 'Quick commands', 'term.blocked': 'Command blocked',
      'term.off': 'Terminal is off. Enable it under Settings → Capabilities.',
      'term.enabled': 'Terminal enabled', 'term.sandbox': 'Sandbox', 'term.timeout': 'Timeout',

      'docs.title': 'Documents', 'docs.sub': 'Presentations, Excel workbooks, Word documents, Markdown and HTML — from your brief, in house style.',
      'docs.kind': 'Format', 'docs.brief': 'Brief', 'docs.briefPh': 'Describe the topic, goal, audience, key figures and structure…',
      'docs.size': 'Length', 'docs.audience': 'Audience', 'docs.tone': 'Tone', 'docs.build': 'Build document',
      'docs.short': 'Short', 'docs.medium': 'Standard', 'docs.long': 'Detailed',
      'docs.mine': 'My documents', 'docs.none': 'No documents yet', 'docs.ready': 'Document ready',
      'docs.preview': 'Preview', 'docs.rebuild': 'Rebuild', 'docs.working': 'Building the document…',
      'docs.slides': 'slides', 'docs.rows': 'rows', 'docs.sheets': 'sheets', 'docs.sections': 'sections',

      'img.title': 'Images', 'img.sub': 'Visuals in house aesthetics: from photorealism to luxury editorial.',
      'img.prompt': 'Prompt', 'img.promptPh': 'What to draw: subject, scene, light, mood, details…',
      'img.negative': 'Exclude', 'img.style': 'Style', 'img.size': 'Size', 'img.count': 'Count',
      'img.generate': 'Generate', 'img.gallery': 'Gallery', 'img.working': 'Generating…',
      'img.done': 'Image ready', 'img.empty': 'Gallery is empty',

      'chess.title': 'Chess', 'chess.sub': 'Play the platform, analyse the position and get a grandmaster comment on every move.',
      'chess.new': 'New game', 'chess.side': 'Your colour', 'chess.white': 'White', 'chess.black': 'Black',
      'chess.random': 'Random', 'chess.level': 'Level', 'chess.mode': 'Mode',
      'chess.modeEngine': 'Play the platform', 'chess.modeAnalysis': 'Position analysis', 'chess.modeHotseat': 'Two at the board',
      'chess.start': 'Start game', 'chess.hint': 'Hint', 'chess.analyse': 'Analyse position',
      'chess.review': 'Game review', 'chess.undo': 'Undo move', 'chess.resign': 'Resign',
      'chess.pgn': 'Export PGN', 'chess.moves': 'Moves', 'chess.eval': 'Evaluation', 'chess.plans': 'Plans',
      'chess.opening': 'Opening', 'chess.yourTurn': 'Your move', 'chess.thinking': 'Platform is thinking…',
      'chess.over': 'Game over', 'chess.win': 'Win', 'chess.lose': 'Loss', 'chess.draw': 'Draw',
      'chess.accuracy': 'Accuracy', 'chess.commentary': 'Commentary', 'chess.noGames': 'No games yet',
      'chess.flip': 'Flip board', 'chess.material': 'Material', 'chess.captured': 'Captured',

      'agent.title': 'Workstation', 'agent.sub': 'Connect your computer so the platform can help with real tasks — under your full control.',
      'agent.off': 'The module is off. Enable it under Settings → Capabilities; it requires explicit consent.',
      'agent.enable': 'Enable module', 'agent.pair': 'Connect device',
      'agent.devices': 'Devices', 'agent.approvals': 'Awaiting approval', 'agent.history': 'Action history',
      'agent.profile': 'Permission profile', 'agent.approve': 'Approve', 'agent.deny': 'Deny',
      'agent.revoke': 'Revoke access', 'agent.download': 'Download agent',
      'agent.consent': 'I understand the platform may perform actions I approve on this device, and I accept the Device Control Policy.',
      'agent.trusted': 'Trusted folders', 'agent.requireApproval': 'Require approval for every action',
      'agent.noDevices': 'No devices connected', 'agent.code': 'Pairing code',
      'agent.deviceKey': 'Device key', 'agent.guarantees': 'Security guarantees',
      'agent.forbidden': 'Always forbidden', 'agent.action': 'Action', 'agent.ask': 'Request action',

      'sec.title': 'Security', 'sec.sub': 'Account protection score, event log and active sessions.',
      'sec.score': 'Protection score', 'sec.events': 'Security events', 'sec.sessions': 'Sessions',
      'sec.audit': 'Activity log', 'sec.revokeAll': 'Sign out everywhere', 'sec.mfa': 'Two-factor authentication',
      'sec.export': 'Export my data', 'sec.erase': 'Delete account',

      'set.title': 'Settings', 'set.profile': 'Profile', 'set.appearance': 'Appearance',
      'set.capabilities': 'Capabilities', 'set.keys': 'API keys', 'set.about': 'About',
      'set.language': 'Interface language', 'set.theme': 'Theme', 'set.animations': 'Animations',
      'set.save': 'Save', 'set.saved': 'Saved', 'set.displayName': 'Display name',
      'set.cap.chat': 'Assistant and chats', 'set.cap.files': 'Directory and files',
      'set.cap.documents': 'Document generation', 'set.cap.images': 'Image generation',
      'set.cap.chess': 'Chess module', 'set.cap.terminal': 'Sandboxed terminal',
      'set.cap.terminal_packages': 'Package installation in terminal', 'set.cap.agent': 'Workstation agent',
      'set.newKey': 'Create key', 'set.keyName': 'Key name', 'set.keyWarn': 'The key is shown once — copy it now.',

      'common.loading': 'Loading…', 'common.error': 'Error', 'common.cancel': 'Cancel',
      'common.close': 'Close', 'common.copy': 'Copy', 'common.copied': 'Copied',
      'common.confirm': 'Confirm', 'common.delete': 'Delete', 'common.save': 'Save',
      'common.open': 'Open', 'common.download': 'Download', 'common.refresh': 'Refresh',
      'common.none': 'none', 'common.yes': 'yes', 'common.no': 'no', 'common.back': 'Back',
      'common.sessionExpired': 'Session expired, please sign in again',
      'common.networkError': 'Cannot reach the platform',
    },
  };

  const MODULES = {
    ru: [
      { id: 'chat', icon: 'spark', title: 'Ассистент', text: 'Диалог с аналитиком, инженером и редактором в одном окне: режимы мышления, потоковые ответы, работа с вашими файлами.', tags: ['6 режимов', 'стриминг', 'файлы как контекст'] },
      { id: 'directory', icon: 'folder', title: 'Директория', text: 'Виртуальная файловая система: папки, версии, поиск, закрепление, корзина и предпросмотр. Всё, что создаёт платформа, попадает сюда.', tags: ['версии', 'поиск', 'превью'] },
      { id: 'terminal', icon: 'terminal', title: 'Терминал', text: 'Настоящая PTY-сессия в изолированной песочнице с политикой безопасности: деструктивные команды блокируются до выполнения.', tags: ['песочница', 'политика', 'журнал'] },
      { id: 'documents', icon: 'presentation', title: 'Документы', text: 'Презентации, книги Excel, документы Word, Markdown и HTML по текстовому ТЗ — в фирменном стиле, с графиками и формулами.', tags: ['PPTX', 'XLSX', 'DOCX', 'MD', 'HTML'] },
      { id: 'images', icon: 'image', title: 'Изображения', text: 'Генерация визуалов с фирменными стилями: люкс, кино, фотореализм, неон, 3D, логотип. Серии до четырёх кадров одним запросом.', tags: ['12 стилей', 'серии', 'сиды'] },
      { id: 'chess', icon: 'chess', title: 'Шахматы', text: 'Партия с платформой любого уровня, оценка позиции, подсказки, разбор партии с точностью и гроссмейстерский комментарий к каждому ходу.', tags: ['анализ', 'разбор', 'PGN', 'дебюты'] },
      { id: 'agent', icon: 'monitor', title: 'Рабочая станция', text: 'Агент на вашем компьютере: состояние системы, окна, файлы в доверенных папках, запуск приложений. Каждое рискованное действие — только с вашего подтверждения.', tags: ['подпись команд', 'профили прав', 'аудит'] },
      { id: 'security', icon: 'shield', title: 'Безопасность', text: 'Индекс защиты, журнал событий, активные сессии, 2FA, API-ключи, экспорт и удаление данных в один клик.', tags: ['2FA', 'аудит', 'GDPR'] },
    ],
    en: [
      { id: 'chat', icon: 'spark', title: 'Assistant', text: 'Analyst, engineer and editor in one window: thinking modes, streaming answers, your files as context.', tags: ['6 modes', 'streaming', 'file context'] },
      { id: 'directory', icon: 'folder', title: 'Directory', text: 'A virtual file system: folders, versions, search, pinning, trash and previews. Everything the platform creates lands here.', tags: ['versions', 'search', 'preview'] },
      { id: 'terminal', icon: 'terminal', title: 'Terminal', text: 'A real PTY session in an isolated sandbox with a security policy: destructive commands are blocked before execution.', tags: ['sandbox', 'policy', 'log'] },
      { id: 'documents', icon: 'presentation', title: 'Documents', text: 'Decks, Excel workbooks, Word documents, Markdown and HTML from a text brief — in house style, with charts and formulas.', tags: ['PPTX', 'XLSX', 'DOCX', 'MD', 'HTML'] },
      { id: 'images', icon: 'image', title: 'Images', text: 'Visual generation with house styles: luxury, cinematic, photoreal, neon, 3D, logo. Series of up to four frames per request.', tags: ['12 styles', 'series', 'seeds'] },
      { id: 'chess', icon: 'chess', title: 'Chess', text: 'Play the platform at any level, evaluate positions, get hints, full game review with accuracy and a grandmaster comment per move.', tags: ['analysis', 'review', 'PGN', 'openings'] },
      { id: 'agent', icon: 'monitor', title: 'Workstation', text: 'An agent on your computer: system state, windows, files in trusted folders, app launching. Every risky action needs your approval.', tags: ['signed commands', 'profiles', 'audit'] },
      { id: 'security', icon: 'shield', title: 'Security', text: 'Protection score, event log, active sessions, 2FA, API keys, one-click data export and erasure.', tags: ['2FA', 'audit', 'GDPR'] },
    ],
  };

  const SECURITY = {
    ru: [
      { icon: 'lock', title: 'Двухслойный хеш пароля', text: 'PBKDF2-SHA256 (210 000 итераций) внутри bcrypt с индивидуальной солью. Утечка базы не даёт готовых паролей.' },
      { icon: 'ticket', title: 'Короткие токены и ротация', text: 'Access-токен живёт 15 минут, refresh-токен ротируется при каждом обновлении и хранится только в виде хеша. Повторное использование токена отзывает всю семью сессий.' },
      { icon: 'pause', title: 'Прогрессивная блокировка', text: 'После шести неудачных попыток входа аккаунт блокируется на 15 минут, событие фиксируется в журнале безопасности.' },
      { icon: 'receipt', title: 'Сквозной журнал аудита', text: 'Каждое значимое действие — вход, файл, команда, документ, действие агента — записывается с актором, IP и исходом.' },
      { icon: 'box', title: 'Песочница терминала', text: 'Команды выполняются в изолированной рабочей области с запретом повышения привилегий, форматирования, reverse-shell и выхода за пределы каталога.' },
      { icon: 'edit', title: 'Подписанные команды агента', text: 'Каждая команда на устройство подписывается HMAC-SHA256 с одноразовым nonce. Агент проверяет подпись, свежесть nonce и собственный профиль прав.' },
      { icon: 'ban', title: 'Запрещённые действия', text: 'Перехват клавиатуры, чтение хранилищ паролей, отключение защиты, форматирование и автозагрузка запрещены на уровне сервера при любом профиле.' },
      { icon: 'filter', title: 'Фильтр ввода и вывода', text: 'Запросы проходят проверку на инъекции промпта и запрещённые темы; ответ модели очищается от исполняемых конструкций перед показом.' },
      { icon: 'clock', title: 'Ограничение частоты', text: 'Скользящее окно на пользователя и действие: чат, генерация, терминал и загрузка файлов имеют собственные лимиты.' },
      { icon: 'globe', title: 'Права субъекта данных', text: 'Экспорт всех данных в машиночитаемом виде и полное удаление аккаунта доступны вам в один клик, без обращения в поддержку.' },
      { icon: 'lock', title: 'Защищённые заголовки', text: 'CSP-совместимые заголовки, nosniff, frame-защита, строгий referrer и изоляция происхождения включены по умолчанию.' },
      { icon: 'key', title: 'Секреты вне кода', text: 'Ключи и строки подключения живут только в переменных окружения, никогда не попадают в клиентский бандл и логи.' },
    ],
    en: [
      { icon: 'lock', title: 'Two-layer password hash', text: 'PBKDF2-SHA256 (210 000 rounds) folded into bcrypt with a per-user salt. A database leak yields no usable passwords.' },
      { icon: 'ticket', title: 'Short tokens and rotation', text: 'Access tokens live 15 minutes; refresh tokens rotate on every renewal and are stored hashed only. Reusing a rotated token revokes the whole session family.' },
      { icon: 'pause', title: 'Progressive lockout', text: 'Six failed sign-ins lock the account for 15 minutes and raise a security event.' },
      { icon: 'receipt', title: 'End-to-end audit log', text: 'Every meaningful action — sign-in, file, command, document, agent action — is recorded with actor, IP and outcome.' },
      { icon: 'box', title: 'Terminal sandbox', text: 'Commands run in an isolated workspace with privilege escalation, formatting, reverse shells and path escape blocked.' },
      { icon: 'edit', title: 'Signed agent commands', text: 'Every command sent to a device is signed with HMAC-SHA256 plus a one-time nonce. The agent verifies signature, nonce freshness and its own profile.' },
      { icon: 'ban', title: 'Forbidden actions', text: 'Keystroke capture, credential-store reads, disabling protection software, formatting and persistence are blocked server-side under any profile.' },
      { icon: 'filter', title: 'Input and output filtering', text: 'Requests are screened for prompt injection and prohibited topics; model output is stripped of executable constructs before rendering.' },
      { icon: 'clock', title: 'Rate limiting', text: 'A sliding window per user and action: chat, generation, terminal and uploads each have their own limits.' },
      { icon: 'globe', title: 'Data subject rights', text: 'Machine-readable export of everything we hold and full account erasure are one click away, no support ticket required.' },
      { icon: 'lock', title: 'Hardened headers', text: 'nosniff, frame protection, strict referrer and origin isolation are enabled by default.' },
      { icon: 'key', title: 'Secrets out of code', text: 'Keys and connection strings live only in environment variables — never in the client bundle or logs.' },
    ],
  };

  const PRICING = {
    ru: [
      { id: 'free', name: 'Старт', price: '0', period: 'навсегда', desc: 'Познакомиться с платформой и закрыть ежедневные задачи.', features: ['Ассистент и 6 режимов мышления', 'Директория, 500 МБ', 'Документы: до 25 в месяц', 'Изображения: до 60 в месяц', 'Шахматы: партия и анализ', 'Терминал: по согласию', 'Агент рабочей станции: наблюдатель'], cta: 'Создать аккаунт' },
      { id: 'pro', name: 'Профессионал', price: '29', period: 'в месяц', desc: 'Для тех, кто каждый день производит документы и решения.', featured: true, badge: 'выбор большинства', features: ['Всё из тарифа «Старт»', 'Директория, 25 ГБ и версии файлов', 'Документы и изображения без месячных лимитов', 'Шахматы: разбор партии и точность', 'Терминал: установка пакетов', 'Агент: профиль «Оператор»', 'API-ключи и приоритетная очередь'], cta: 'Перейти на Pro' },
      { id: 'enterprise', name: 'Предприятие', price: 'по запросу', period: '', desc: 'Командная работа, расширенный контроль и сопровождение.', features: ['Всё из тарифа «Профессионал»', 'Агент: профиль «Полный контроль»', 'SSO, роли и политики команды', 'Расширенный аудит и экспорт журналов', 'Выделенные лимиты и SLA', 'Соглашение об обработке данных (DPA)'], cta: 'Связаться с нами' },
    ],
    en: [
      { id: 'free', name: 'Start', price: '0', period: 'forever', desc: 'Get to know the platform and cover daily tasks.', features: ['Assistant and 6 thinking modes', 'Directory, 500 MB', 'Documents: up to 25 per month', 'Images: up to 60 per month', 'Chess: play and analyse', 'Terminal: with consent', 'Workstation agent: observer'], cta: 'Create account' },
      { id: 'pro', name: 'Professional', price: '29', period: 'per month', desc: 'For those who produce documents and decisions every day.', featured: true, badge: 'most chosen', features: ['Everything in Start', 'Directory, 25 GB and file versions', 'Unlimited documents and images', 'Chess: game review and accuracy', 'Terminal: package installation', 'Agent: operator profile', 'API keys and priority queue'], cta: 'Go Pro' },
      { id: 'enterprise', name: 'Enterprise', price: 'on request', period: '', desc: 'Team work, extended control and support.', features: ['Everything in Professional', 'Agent: full control profile', 'SSO, team roles and policies', 'Extended audit and log export', 'Dedicated limits and SLA', 'Data processing agreement (DPA)'], cta: 'Contact us' },
    ],
  };

  const store = {
    lang: localStorage.getItem('mst.lang') || (navigator.language || 'ru').slice(0, 2).toLowerCase(),
  };
  if (!DICT[store.lang]) store.lang = (store.lang || '').startsWith('en') ? 'en' : 'ru';

  function t(key, fallback) {
    const table = DICT[store.lang] || DICT.ru;
    return table[key] || DICT.ru[key] || fallback || key;
  }

  function setLang(lang) {
    store.lang = DICT[lang] ? lang : (lang || '').startsWith('en') ? 'en' : 'ru';
    localStorage.setItem('mst.lang', store.lang);
    document.documentElement.lang = store.lang;
    apply();
    document.dispatchEvent(new CustomEvent('mst:lang', { detail: { lang: store.lang } }));
  }

  function apply(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach((el) => {
      const key = el.getAttribute('data-i18n');
      const value = t(key);
      if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
        if (el.getAttribute('data-i18n-ph') === '1' || el.placeholder) el.placeholder = value;
        else el.value = value;
      } else {
        el.textContent = value;
      }
    });
    scope.querySelectorAll('[data-i18n-ph]').forEach((el) => {
      el.setAttribute('placeholder', t(el.getAttribute('data-i18n-ph')));
    });
    scope.querySelectorAll('[data-i18n-title]').forEach((el) => {
      el.setAttribute('title', t(el.getAttribute('data-i18n-title')));
    });
    document.querySelectorAll('.lang-opt').forEach((el) => {
      el.classList.toggle('active', el.dataset.lang === store.lang);
    });
  }

  function fmtBytes(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024) return `${n} Б`;
    if (n < 1048576) return `${(n / 1024).toFixed(1)} КБ`;
    if (n < 1073741824) return `${(n / 1048576).toFixed(1)} МБ`;
    return `${(n / 1073741824).toFixed(2)} ГБ`;
  }

  function fmtDate(value) {
    if (!value) return '—';
    const d = new Date(String(value).includes('T') ? value : String(value).replace(' ', 'T') + 'Z');
    if (Number.isNaN(d.getTime())) return String(value).slice(0, 16);
    return d.toLocaleString(store.lang === 'ru' ? 'ru-RU' : 'en-GB', {
      day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  }

  function relative(value) {
    if (!value) return '—';
    const d = new Date(String(value).includes('T') ? value : String(value).replace(' ', 'T') + 'Z');
    const diff = (Date.now() - d.getTime()) / 1000;
    const ru = store.lang === 'ru';
    if (diff < 60) return ru ? 'только что' : 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)} ${ru ? 'мин назад' : 'min ago'}`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} ${ru ? 'ч назад' : 'h ago'}`;
    if (diff < 604800) return `${Math.floor(diff / 86400)} ${ru ? 'дн назад' : 'd ago'}`;
    return fmtDate(value);
  }

  global.I18N = { t, setLang, apply, get lang() { return store.lang; }, dict: DICT, MODULES, SECURITY, PRICING, fmtBytes, fmtDate, relative };
})(window);
