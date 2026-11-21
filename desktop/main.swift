import AppKit
import WebKit
import UniformTypeIdentifiers

final class Desktop: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandlerWithReply {
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

    func applicationDidFinishLaunching(_ notification: Notification) {
        installMenus()
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
        status("Opening your workspace…", retryable: false)
        guard let pythonURL = python() else {
            status("Install Python 3.9 or newer to open the workspace.", retryable: true)
            return
        }
        guard let resources = Bundle.main.resourceURL,
              let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
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
                if self.restartRequested && !self.quitting {
                    self.restartRequested = false
                    self.startService()
                } else if !self.quitting {
                    self.status("The local service stopped (status \(finished.terminationStatus)). If another copy has this workspace open, quit it before retrying.", retryable: true)
                }
            }
        }
        do {
            try process.run()
            service = process
            lifetime = life
        } catch {
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
        }
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
        window.makeFirstResponder(webView)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled && service?.isRunning == true {
            status("The dashboard could not load from the local service.", retryable: true)
        }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if service?.isRunning == true { webView.reload() }
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
