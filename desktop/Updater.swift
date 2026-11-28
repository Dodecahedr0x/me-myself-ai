import AppKit
import CryptoKit

/// Keeps a copy installed in Applications current from this repository's GitHub releases. See docs/specs/app-updates.md.
@MainActor
final class Updater {
    static let feed = URL(string: "https://api.github.com/repos/Dodecahedr0x/me-myself-ai/releases/latest")!
    nonisolated static let identifier = "local.memyselfai.desktop"
    #if arch(arm64)
    static let asset = "MeMyselfAI-macos-arm64.zip"
    #else
    static let asset = "MeMyselfAI-macos-x86_64.zip"
    #endif

    /// Where the updater stands, shown in Settings → Workspace as well as the menu.
    enum Phase: String {
        case idle, checking, upToDate = "up_to_date", available, downloading, installing, restarting, failed
        /// Not in Applications, or the folder can't be written: it checks but never installs.
        case unavailable
    }

    /// The status line shown in the menu bar menu.
    private(set) var line = ""
    private(set) var phase = Phase.idle
    /// The newer release found by the last check, if any.
    private(set) var latest: String?
    private(set) var checkedAt: Date?
    var onChange: (() -> Void)?
    /// Called once the new bundle is in place. The app quits and the helper reopens it.
    var onInstalled: (() -> Void)?

    let current: String
    private let bundle = Bundle.main.bundleURL.resolvingSymlinksInPath()
    private let feed: URL
    private var busy = false
    private var timer: Timer?
    /// The release a check found, kept so Settings can install it without asking GitHub again.
    private var found: Found?

    private struct Found {
        let version: String
        let release: Release
        let asset: Release.Asset
    }

    init() {
        current = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
        var feed = Updater.feed
        if let custom = ProcessInfo.processInfo.environment["MEMYSELFAI_UPDATE_FEED"].flatMap(URL.init(string:)),
           Updater.allowed(custom) {
            feed = custom
        }
        self.feed = feed
        if installed {
            set(.idle, "Version \(current)")
        } else {
            set(.unavailable, "Version \(current) · updates only from Applications")
        }
    }

    /// Only a copy in /Applications or ~/Applications updates itself, so builds in checkouts run their own code.
    var installed: Bool {
        let parent = bundle.deletingLastPathComponent().path
        let home = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications").resolvingSymlinksInPath().path
        return parent == "/Applications" || parent == home
    }

    /// What Settings shows. Plain JSON types only, so it can cross into the page.
    var snapshot: [String: Any] {
        var out: [String: Any] = ["version": current, "phase": phase.rawValue, "line": line, "installed": installed]
        if let latest { out["latest"] = latest }
        if let checkedAt { out["checked_at"] = checkedAt.timeIntervalSince1970 }
        return out
    }

    func start() {
        guard installed else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 30) { [weak self] in self?.check() }
        timer = Timer.scheduledTimer(withTimeInterval: 6 * 3600, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.check() }
        }
    }

    /// Looks for a newer release. Automatic checks and the menu install it at once; Settings passes
    /// `install: false` so the user sees the version first and installs it with `install()`.
    func check(install: Bool = true) {
        guard installed else {
            set(.unavailable, "Version \(current) · updates only from Applications")
            return
        }
        guard !busy else { return }
        busy = true
        set(.checking, "Version \(current) · checking for updates…")
        Task {
            do {
                if let found = try await find(), install {
                    try await self.install(found)
                }
            } catch {
                set(.failed, "Version \(current) · update failed: \(error.localizedDescription)")
            }
            busy = false
        }
    }

    /// Installs the release the last check found, or checks and installs when there is none.
    func install() {
        guard let found, installed, !busy else { return check(install: true) }
        busy = true
        Task {
            do {
                try await install(found)
            } catch {
                set(.failed, "Version \(current) · update failed: \(error.localizedDescription)")
            }
            busy = false
        }
    }

    private func set(_ phase: Phase, _ text: String) {
        self.phase = phase
        line = text
        onChange?()
    }

    private static func allowed(_ url: URL) -> Bool {
        url.scheme == "https" || (url.scheme == "http" && url.host == "127.0.0.1")
    }

    static func numbers(_ version: String) -> [Int]? {
        let trimmed = version.hasPrefix("v") ? String(version.dropFirst()) : version
        let parts = trimmed.split(separator: ".", omittingEmptySubsequences: false).map { Int($0) }
        guard !parts.isEmpty, parts.allSatisfy({ $0 != nil }) else { return nil }
        return parts.map { $0! }
    }

    static func newer(_ candidate: String, than current: String) -> Bool {
        guard let a = numbers(candidate), let b = numbers(current) else { return false }
        for i in 0..<max(a.count, b.count) {
            let x = i < a.count ? a[i] : 0, y = i < b.count ? b[i] : 0
            if x != y { return x > y }
        }
        return false
    }

    private struct Release: Decodable {
        struct Asset: Decodable {
            let name: String
            let browser_download_url: URL
            let digest: String?
        }
        let tag_name: String
        let assets: [Asset]
    }

    private struct Failure: LocalizedError {
        let errorDescription: String?
        init(_ text: String) { errorDescription = text }
    }

    private func request(_ url: URL) -> URLRequest {
        var request = URLRequest(url: url, timeoutInterval: 60)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue("MeMyselfAI-updater", forHTTPHeaderField: "User-Agent")
        return request
    }

    /// Reads the feed. Returns the newer release when this copy can install it; otherwise sets the line and returns nil.
    private func find() async throws -> Found? {
        let (body, response) = try await URLSession.shared.data(for: request(feed))
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else {
            if status == 404 {
                checked(nil)
                set(.upToDate, "Version \(current) · up to date")
                return nil
            }
            throw Failure("GitHub answered \(status)")
        }
        let release = try JSONDecoder().decode(Release.self, from: body)
        let version = release.tag_name.hasPrefix("v") ? String(release.tag_name.dropFirst()) : release.tag_name
        guard Updater.newer(version, than: current),
              let asset = release.assets.first(where: { $0.name == Updater.asset }) else {
            checked(nil)
            set(.upToDate, "Version \(current) · up to date")
            return nil
        }
        let fm = FileManager.default
        guard fm.isWritableFile(atPath: bundle.deletingLastPathComponent().path), fm.isWritableFile(atPath: bundle.path) else {
            checked(nil)
            latest = version
            set(.unavailable, "Version \(version) available · this folder can't be written")
            return nil
        }
        let result = Found(version: version, release: release, asset: asset)
        checked(result)
        set(.available, "Version \(current) · version \(version) available")
        return result
    }

    private func checked(_ result: Found?) {
        found = result
        latest = result?.version
        checkedAt = Date()
    }

    private func install(_ found: Found) async throws {
        let version = found.version, asset = found.asset
        let fm = FileManager.default
        guard Updater.allowed(asset.browser_download_url) else { throw Failure("unexpected download address") }
        let expected = try await digest(of: asset, in: found.release)

        set(.downloading, "Downloading version \(version)…")
        let staging = try fm.url(for: .itemReplacementDirectory, in: .userDomainMask, appropriateFor: bundle, create: true)
        defer { try? fm.removeItem(at: staging) }
        let (download, downloadResponse) = try await URLSession.shared.download(for: request(asset.browser_download_url))
        guard (downloadResponse as? HTTPURLResponse)?.statusCode == 200 else {
            throw Failure("download answered \((downloadResponse as? HTTPURLResponse)?.statusCode ?? 0)")
        }
        let zip = staging.appendingPathComponent(asset.name)
        try fm.moveItem(at: download, to: zip)

        set(.installing, "Installing version \(version)…")
        let fresh = try await Task.detached { () throws -> URL in
            guard try Updater.sha256(zip) == expected else { throw Failure("the download doesn't match its published hash") }
            let expanded = staging.appendingPathComponent("expanded")
            guard Updater.run("/usr/bin/ditto", ["-x", "-k", zip.path, expanded.path]) == 0 else {
                throw Failure("the download couldn't be expanded")
            }
            let items = try FileManager.default.contentsOfDirectory(atPath: expanded.path).filter { $0 != "__MACOSX" }
            guard items == ["MeMyselfAI.app"] else { throw Failure("the download doesn't hold one MeMyselfAI.app") }
            let app = expanded.appendingPathComponent("MeMyselfAI.app")
            guard Updater.run("/usr/bin/codesign", ["--verify", "--deep", "--strict", app.path]) == 0 else {
                throw Failure("the new app's signature is broken")
            }
            guard let info = Bundle(url: app), info.bundleIdentifier == Updater.identifier,
                  info.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String == version else {
                throw Failure("the new app isn't MeMyselfAI \(version)")
            }
            return app
        }.value
        _ = try fm.replaceItemAt(bundle, withItemAt: fresh)
        set(.restarting, "Restarting into version \(version)…")
        relaunch()
        onInstalled?()
    }

    /// The asset's SHA-256, from the API's digest field or the release's .sha256 file.
    private func digest(of asset: Release.Asset, in release: Release) async throws -> String {
        if let digest = asset.digest, digest.hasPrefix("sha256:") {
            return String(digest.dropFirst(7)).lowercased()
        }
        guard let file = release.assets.first(where: { $0.name == asset.name + ".sha256" }),
              Updater.allowed(file.browser_download_url) else {
            throw Failure("the release publishes no hash for \(asset.name)")
        }
        let (body, response) = try await URLSession.shared.data(for: request(file.browser_download_url))
        guard (response as? HTTPURLResponse)?.statusCode == 200,
              let text = String(data: body, encoding: .utf8),
              let hash = text.split(whereSeparator: { $0 == " " || $0 == "\n" }).first,
              hash.count == 64 else {
            throw Failure("the release's hash file is unreadable")
        }
        return hash.lowercased()
    }

    nonisolated static func sha256(_ file: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: file)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1 << 20), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    nonisolated static func run(_ path: String, _ arguments: [String]) -> Int32 {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: path)
        process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return -1 }
        process.waitUntilExit()
        return process.terminationStatus
    }

    /// A detached shell waits for this process to exit, then opens the replaced bundle with the same arguments.
    private func relaunch() {
        let helper = Process()
        helper.executableURL = URL(fileURLWithPath: "/bin/sh")
        let script = "pid=$1; app=$2; shift 2; while kill -0 \"$pid\" 2>/dev/null; do sleep 0.5; done; exec /usr/bin/open \"$app\" --args \"$@\""
        helper.arguments = ["-c", script, "relaunch", String(ProcessInfo.processInfo.processIdentifier), bundle.path]
            + Array(CommandLine.arguments.dropFirst())
        helper.standardInput = FileHandle.nullDevice
        helper.standardOutput = FileHandle.nullDevice
        helper.standardError = FileHandle.nullDevice
        try? helper.run()
    }
}
