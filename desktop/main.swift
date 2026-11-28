import AppKit
import IOKit.pwr_mgt
import WebKit
import UniformTypeIdentifiers
import UserNotifications

final class Desktop: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandlerWithReply,
    UNUserNotificationCenterDelegate {
    private var window: NSWindow!
    private var view: WKWebView!
    private var message: NSTextField!
    private var retry: NSButton!
    private var statusView: NSStackView!
    private var service: Process?
    private var lifetime: Pipe?
    private var origin: URL?
    private var quitting = false
    private var restartRequested = false
    // The menu bar icon shows whether the local services are running. See desktop-runtime.md.
    private var statusItem: NSStatusItem!
    private var stateItem: NSMenuItem!
    // Installed copies update themselves from GitHub releases. See app-updates.md.
    private var updater: Updater!
    private var updateItem: NSMenuItem!
    // Sleep is held for up to 25 seconds while the service hands work to a paired instance.
    private var powerPort: io_connect_t = 0
    private var powerNotify: IONotificationPortRef?
    private var powerNotifier: io_object_t = 0
    private var pendingSleep: Int?
    private var sleepAck: DispatchSourceSignal?
    // A task to open once the dashboard has loaded, from a clicked notification. See dashboard.md#notifications.
    private var pendingTask: String?
    // Permission is asked once per launch, at the first notification.
    private var notificationsAllowed: Bool?
    private var waitingNotifications: [UNNotificationRequest] = []

    func applicationWillFinishLaunching(_ notification: Notification) {
        // Set before launch finishes, so a click that launched the app is delivered.
        UNUserNotificationCenter.current().delegate = self
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        updater = Updater()
        installMenus()
        installStatusItem()
        updater.onChange = { [weak self] text in self?.updateItem.title = text }
        // Quit from the run loop, not from inside the updater's main-actor task. A quit that waits for the
        // service runs a nested event loop, and main-queue work (the service's exit) can't run inside a main-queue job.
        updater.onInstalled = { RunLoop.main.perform { NSApp.terminate(nil) } }
        updater.start()
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "chooseRepository")
        view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        view.uiDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1190, height: 780),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "MeMyselfAI"
        window.minSize = NSSize(width: 450, height: 420)
        window.isReleasedWhenClosed = false
        window.setFrameAutosaveName("MeMyselfAI Workspace")
        window.center()
        let container = NSView()
        window.contentView = container
        container.addSubview(view)
        message = NSTextField(wrappingLabelWithString: "")
        message.alignment = .center
        retry = NSButton(title: "Try Again", target: self, action: #selector(retryService))
        statusView = NSStackView(views: [message, retry])
        statusView.orientation = .vertical
        statusView.spacing = 16
        container.addSubview(statusView)
        view.translatesAutoresizingMaskIntoConstraints = false
        statusView.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            view.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            view.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            view.topAnchor.constraint(equalTo: container.topAnchor),
            view.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            statusView.centerXAnchor.constraint(equalTo: container.centerXAnchor),
            statusView.centerYAnchor.constraint(equalTo: container.centerYAnchor),
            statusView.widthAnchor.constraint(lessThanOrEqualTo: container.widthAnchor, constant: -64)
        ])
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        startService()
        registerPower()
    }

    private func registerPower() {
        signal(SIGUSR1, SIG_IGN)
        let ack = DispatchSource.makeSignalSource(signal: SIGUSR1, queue: .main)
        ack.setEventHandler { [weak self] in self?.allowSleep() }
        ack.resume()
        sleepAck = ack
        let refcon = Unmanaged.passUnretained(self).toOpaque()
        powerPort = IORegisterForSystemPower(refcon, &powerNotify, { refcon, _, type, argument in
            guard let refcon = refcon else { return }
            Unmanaged<Desktop>.fromOpaque(refcon).takeUnretainedValue().power(type, argument)
        }, &powerNotifier)
        if powerPort != 0, let port = powerNotify {
            CFRunLoopAddSource(CFRunLoopGetMain(), IONotificationPortGetRunLoopSource(port).takeUnretainedValue(),
                               .commonModes)
        }
    }

    private func power(_ type: natural_t, _ argument: UnsafeMutableRawPointer?) {
        let canSleep: natural_t = 0xE000_0270, willSleep: natural_t = 0xE000_0280
        let id = Int(bitPattern: argument)
        if type == canSleep {
            IOAllowPowerChange(powerPort, id)
        } else if type == willSleep {
            guard let process = service, process.isRunning else {
                IOAllowPowerChange(powerPort, id)
                return
            }
            allowSleep()
            pendingSleep = id
            kill(process.processIdentifier, SIGUSR2)
            DispatchQueue.main.asyncAfter(deadline: .now() + 25) { [weak self] in self?.allowSleep() }
        }
    }

    private func allowSleep() {
        if let id = pendingSleep {
            pendingSleep = nil
            IOAllowPowerChange(powerPort, id)
        }
    }

    private func installMenus() {
        let bar = NSMenu()
        func menu(_ title: String) -> NSMenu {
            let item = NSMenuItem()
            let submenu = NSMenu(title: title)
            item.submenu = submenu
            bar.addItem(item)
            return submenu
        }
        let app = menu("MeMyselfAI")
        app.addItem(withTitle: "About MeMyselfAI", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: "Check for Updates…", action: #selector(checkForUpdates), keyEquivalent: "").target = self
        app.addItem(.separator())
        app.addItem(withTitle: "Hide MeMyselfAI", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        app.addItem(.separator())
        app.addItem(withTitle: "Quit MeMyselfAI", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let edit = menu("Edit")
        for (name, action, key) in [("Undo", "undo:", "z"), ("Redo", "redo:", "Z"),
                                    ("Cut", "cut:", "x"), ("Copy", "copy:", "c"),
                                    ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")] {
            edit.addItem(withTitle: name, action: Selector(action), keyEquivalent: key)
        }
        let windowMenu = menu("Window")
        windowMenu.addItem(withTitle: "Show Dashboard", action: #selector(showDashboard), keyEquivalent: "1").target = self
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        NSApp.windowsMenu = windowMenu
        NSApp.mainMenu = bar
    }

    private enum ServiceState { case starting, running, stopping, stopped }

    private func installStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        let menu = NSMenu()
        stateItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
        stateItem.isEnabled = false
        menu.addItem(stateItem)
        updateItem = NSMenuItem(title: updater.line, action: nil, keyEquivalent: "")
        updateItem.isEnabled = false
        menu.addItem(updateItem)
        menu.addItem(.separator())
        menu.addItem(withTitle: "Open Dashboard", action: #selector(showDashboard), keyEquivalent: "").target = self
        menu.addItem(withTitle: "Restart Services", action: #selector(retryService), keyEquivalent: "").target = self
        menu.addItem(withTitle: "Check for Updates…", action: #selector(checkForUpdates), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit MeMyselfAI", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        statusItem.menu = menu
        setState(.starting)
    }

    private func setState(_ state: ServiceState) {
        let text: String
        switch state {
        case .starting: text = "Services starting…"
        case .running: text = "Services running"
        case .stopping: text = "Stopping services…"
        case .stopped: text = "Services stopped"
        }
        let image = Desktop.logo(badge: state == .stopped)
        image.accessibilityDescription = "MeMyselfAI: " + text
        statusItem.button?.image = image
        // The logo is full strength only while the services run.
        statusItem.button?.appearsDisabled = state != .running
        statusItem.button?.toolTip = "MeMyselfAI: " + text
        stateItem.title = text
    }

    /// The app logo as a menu bar template: a tilted rounded square with three slanted bars cut out.
    private static func logo(badge: Bool) -> NSImage {
        let image = NSImage(size: NSSize(width: 18, height: 18), flipped: false) { _ in
            guard let context = NSGraphicsContext.current else { return false }
            context.saveGraphicsState()
            let tilt = NSAffineTransform()
            tilt.translateX(by: 9, yBy: 9)
            tilt.rotate(byDegrees: 4)
            tilt.concat()
            NSColor.black.setFill()
            NSColor.black.setStroke()
            NSBezierPath(roundedRect: NSRect(x: -7.5, y: -7.5, width: 15, height: 15), xRadius: 4, yRadius: 4).fill()
            context.compositingOperation = .destinationOut
            for (top, bottom) in [(NSPoint(x: -2.0, y: 2.4), NSPoint(x: -2.9, y: -2.3)),
                                  (NSPoint(x: 0.9, y: 3.8), NSPoint(x: -1.0, y: -3.8)),
                                  (NSPoint(x: 2.8, y: 2.7), NSPoint(x: 1.6, y: -2.7))] {
                let bar = NSBezierPath()
                bar.move(to: top)
                bar.line(to: bottom)
                bar.lineWidth = 1.8
                bar.lineCapStyle = .round
                bar.stroke()
            }
            context.restoreGraphicsState()
            if badge {
                context.compositingOperation = .destinationOut
                NSBezierPath(ovalIn: NSRect(x: 11, y: 0, width: 7, height: 7)).fill()
                context.compositingOperation = .sourceOver
                NSColor.black.setFill()
                NSBezierPath(ovalIn: NSRect(x: 12.5, y: 1.5, width: 4, height: 4)).fill()
            }
            return true
        }
        image.isTemplate = true
        return image
    }

    private func status(_ text: String, retryable: Bool) {
        view.isHidden = true
        statusView.isHidden = false
        message.stringValue = text
        retry.isHidden = !retryable
    }

    private func python() -> URL? {
        let candidates = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            let probe = Process()
            probe.executableURL = URL(fileURLWithPath: path)
            probe.arguments = ["-I", "-c", "import sys; sys.exit(sys.version_info < (3, 9))"]
            probe.standardOutput = FileHandle.nullDevice
            probe.standardError = FileHandle.nullDevice
            do {
                try probe.run()
                probe.waitUntilExit()
                if probe.terminationStatus == 0 { return probe.executableURL }
            } catch { continue }
        }
        return nil
    }

    @objc private func startService() {
        guard service == nil else { return }
        origin = nil
        setState(.starting)
        status("Opening your workspace…", retryable: false)
        guard let pythonURL = python() else {
            setState(.stopped)
            status("Install Python 3.9 or newer to open the workspace.", retryable: true)
            return
        }
        guard let resources = Bundle.main.resourceURL,
              let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            setState(.stopped)
            status("The application bundle or Application Support folder is unavailable.", retryable: true)
            return
        }
        let arguments = CommandLine.arguments
        let data: String
        if let option = arguments.firstIndex(of: "--data"), option + 1 < arguments.count {
            data = URL(fileURLWithPath: arguments[option + 1]).standardizedFileURL.path
        } else {
            data = support.appendingPathComponent("MeMyselfAI").path
        }
        let process = Process()
        process.executableURL = pythonURL
        process.arguments = ["-I", "-B", resources.appendingPathComponent("launch_service.py").path, "--data", data]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        process.environment = env
        let output = Pipe()
        let life = Pipe()
        process.standardInput = life
        process.standardOutput = output
        process.standardError = FileHandle.standardError
        process.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async {
                guard let self = self, self.service === finished else { return }
                self.service = nil
                self.lifetime = nil
                self.origin = nil
                if self.quitting {
                    NSApp.reply(toApplicationShouldTerminate: true)
                } else if self.restartRequested {
                    self.restartRequested = false
                    self.startService()
                } else {
                    self.setState(.stopped)
                    self.status("The local service stopped (status \(finished.terminationStatus)). If another copy has this workspace open, quit it before retrying.", retryable: true)
                }
            }
        }
        do {
            try process.run()
            service = process
            lifetime = life
        } catch {
            setState(.stopped)
            status("The bundled local service could not start.", retryable: true)
            return
        }
        // Read a single bounded launch line off the UI thread. Its ticket stays in this process.
        DispatchQueue.global(qos: .userInitiated).async {
            let reader = output.fileHandleForReading
            var bytes = Data()
            while bytes.count < 4096, bytes.firstIndex(of: 10) == nil {
                let chunk = reader.availableData
                if chunk.isEmpty { break }
                bytes.append(chunk)
            }
            let line = bytes.prefix(while: { $0 != 10 })
            let url = line.count < 4096 ? String(data: line, encoding: .utf8).flatMap(URL.init(string:)) : nil
            DispatchQueue.main.async {
                guard self.service === process, process.isRunning else { return }
                guard let url = url, url.scheme == "http", url.host == "127.0.0.1",
                      url.user == nil, url.password == nil, url.port != nil,
                      url.path.hasPrefix("/launch/") else {
                    process.terminate()
                    return
                }
                self.origin = url
                self.view.load(URLRequest(url: url))
            }
            // Every later line the service writes is a notification to post. See dashboard.md#notifications.
            var rest = Data(bytes.drop(while: { $0 != 10 }).dropFirst())
            while true {
                while let end = rest.firstIndex(of: 10) {
                    let line = rest[rest.startIndex..<end]
                    rest = Data(rest[rest.index(after: end)...])
                    DispatchQueue.main.async { self.notify(line) }
                }
                if rest.count > 65536 { rest.removeAll() }
                let chunk = reader.availableData
                if chunk.isEmpty { break }
                rest.append(chunk)
            }
        }
    }

    private func notify(_ line: Data) {
        let prefix = Data("notify ".utf8)
        guard line.starts(with: prefix),
              let item = try? JSONSerialization.jsonObject(with: line.dropFirst(prefix.count)) as? [String: Any],
              let task = item["task"] as? String, let key = item["key"] as? String,
              let title = item["title"] as? String, let body = item["body"] as? String else { return }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        content.userInfo = ["task": task]
        // The service sends each task state once; the identifier also stops the system showing a version twice.
        let request = UNNotificationRequest(identifier: key, content: content, trigger: nil)
        let center = UNUserNotificationCenter.current()
        switch notificationsAllowed {
        case true?: center.add(request)
        case false?: return
        case nil:
            // Ask once, at the first notification; hold the ones that arrive meanwhile.
            waitingNotifications.append(request)
            guard waitingNotifications.count == 1 else { return }
            center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
                DispatchQueue.main.async {
                    self.notificationsAllowed = granted
                    if granted { self.waitingNotifications.forEach { center.add($0) } }
                    self.waitingNotifications.removeAll()
                }
            }
        }
    }

    // Shown unless the dashboard is the focused window, where the task is already in view.
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        DispatchQueue.main.async {
            let focused = NSApp.isActive && self.window?.isKeyWindow == true && self.window?.isVisible == true
            completionHandler(focused ? [] : [.banner, .list, .sound])
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let task = response.notification.request.content.userInfo["task"] as? String
        DispatchQueue.main.async {
            if let task = task {
                self.pendingTask = task
                if self.window != nil { self.showDashboard() }
                self.openPendingTask()
            }
            completionHandler()
        }
    }

    private func openPendingTask() {
        guard let task = pendingTask, let view = view, let url = view.url, sameOrigin(url),
              let data = try? JSONSerialization.data(withJSONObject: [task]),
              let argument = String(data: data, encoding: .utf8) else { return }
        pendingTask = nil
        view.evaluateJavaScript("window.openTaskFromNotification && openTaskFromNotification(...\(argument))")
    }

    @objc private func retryService() {
        if let process = service {
            restartRequested = true
            if process.isRunning { process.terminate() }
        } else {
            startService()
        }
    }

    private func sameOrigin(_ url: URL) -> Bool {
        guard let origin = origin else { return false }
        return url.scheme == origin.scheme && url.host == origin.host && url.port == origin.port
            && url.user == nil && url.password == nil
    }

    // WebKit hides absolute filesystem paths. Return the directory selected in this sheet.
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        guard message.name == "chooseRepository", message.webView === view,
              message.frameInfo.isMainFrame, let url = message.frameInfo.request.url, sameOrigin(url),
              window.attachedSheet == nil else {
            replyHandler(nil, "The folder picker is unavailable.")
            return
        }
        let panel = NSOpenPanel()
        if let checkout = message.body as? String, checkout.hasPrefix("/") {
            panel.directoryURL = URL(fileURLWithPath: checkout, isDirectory: true)
            panel.message = "Choose the registered repository folder to check access for MeMyselfAI."
        }
        panel.prompt = "Choose Folder"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = false
        panel.beginSheetModal(for: window) { result in
            replyHandler(result == .OK ? panel.url?.path ?? "" : "", nil)
        }
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        guard webView === view, frame.isMainFrame, let url = frame.request.url, sameOrigin(url),
              window.attachedSheet == nil else { completionHandler(nil); return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.json]
        panel.beginSheetModal(for: window) { result in
            completionHandler(result == .OK ? panel.urls : nil)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if sameOrigin(url) {
            if action.targetFrame == nil {
                decisionHandler(.cancel)
                if action.navigationType == .linkActivated { webView.load(URLRequest(url: url)) }
            } else {
                decisionHandler(.allow)
            }
            return
        }
        decisionHandler(.cancel)
        if action.navigationType == .linkActivated, ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
           url.host != nil, url.user == nil, url.password == nil {
            NSWorkspace.shared.open(url)
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard let url = webView.url, sameOrigin(url) else { return }
        statusView.isHidden = true
        webView.isHidden = false
        setState(.running)
        window.makeFirstResponder(webView)
        openPendingTask()
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled && service?.isRunning == true {
            status("The dashboard could not load from the local service.", retryable: true)
        }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if service?.isRunning == true { webView.reload() }
    }

    @objc private func checkForUpdates() {
        updater.check()
    }

    @objc private func showDashboard() {
        window.deminiaturize(nil)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showDashboard()
        return true
    }

    // Quitting waits for the service to stop its agents and exit, so no work keeps running unseen.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let process = service, process.isRunning else { return .terminateNow }
        quitting = true
        setState(.stopping)
        try? lifetime?.fileHandleForWriting.close()
        process.terminate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 30) {
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
        }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        try? lifetime?.fileHandleForWriting.close()
        if service?.isRunning == true { service?.terminate() }
    }
}

let application = NSApplication.shared
let desktop = Desktop()
application.delegate = desktop
application.setActivationPolicy(.regular)
application.run()
