// VoiceNotch — 菜单栏常驻图标 + 灵动岛式的语音播报状态条。
//
// 两件事：
//   1. 菜单栏图标：随时点开就能静音、跳过当前段、改几个开关——
//      不用去翻 JSON，也不用记 hook 的命令行参数。
//   2. 刘海下沿的状态条：谁在说话、后面还排着几段，听得到也看得到。
//
// 播报状态来自 tts_daemon.py 写的状态文件，只读不写，250ms 轮询一次
// （mtime 不变就跳过）。设置写回 ~/.claude/voice_config.json，
// 只改动到的那个键，别人的字段原样保留。
//
// 编译（install.py 会自动做）：
//   swiftc -O VoiceNotch.swift -o ~/.claude/hooks/voice-notch

import AppKit
import SwiftUI

// 刘海下方那块常驻把手的尺寸
let HANDLE = (width: 46.0 as CGFloat, height: 15.0 as CGFloat)

let claudeDir = NSString(string: "~/.claude").expandingTildeInPath
let statusPath = claudeDir + "/.voice_status.json"
let configPath = claudeDir + "/voice_config.json"
let sockPath = claudeDir + "/.voice_tts.sock"

// --------------------------------------------------------------------------
// 配置文件：hook 和守护进程都在读同一份，只能改键，不能整份覆盖
// --------------------------------------------------------------------------

enum Config {
    static func load() -> [String: Any] {
        guard let data = FileManager.default.contents(atPath: configPath),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return [:] }
        return obj
    }

    static func flag(_ dict: [String: Any], _ key: String, _ fallback: Bool) -> Bool {
        dict[key] as? Bool ?? fallback
    }

    /// 改一个键写回去。先写临时文件再 rename——hook 每轮都在读这份配置，
    /// 读到半截 JSON 会退回默认值，那一轮的设置就白改了。
    @discardableResult
    static func set(_ key: String, _ value: Bool) -> Bool {
        var obj = load()
        obj[key] = value
        guard let data = try? JSONSerialization.data(
            withJSONObject: obj,
            options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        ) else { return false }
        let tmp = configPath + ".tmp"
        guard (try? data.write(to: URL(fileURLWithPath: tmp))) != nil else { return false }
        return rename(tmp, configPath) == 0
    }

    static func mtime() -> Date {
        let attrs = try? FileManager.default.attributesOfItem(atPath: configPath)
        return (attrs?[.modificationDate] as? Date) ?? .distantPast
    }
}

// --------------------------------------------------------------------------
// 守护进程：静音要立刻生效，光写配置得等当前这段播完
// --------------------------------------------------------------------------

enum Daemon {
    /// 发一条命令过去。服务没起来就当无事发生——设置已经落在配置文件里，
    /// 下次它起来照样读得到。
    @discardableResult
    static func send(_ command: String) -> Bool {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }

        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let path = Array(sockPath.utf8)
        let capacity = MemoryLayout.size(ofValue: addr.sun_path)
        guard path.count < capacity else { return false }
        withUnsafeMutablePointer(to: &addr.sun_path) {
            $0.withMemoryRebound(to: CChar.self, capacity: capacity) { dst in
                for (i, byte) in path.enumerated() { dst[i] = CChar(bitPattern: byte) }
                dst[path.count] = 0
            }
        }
        let size = socklen_t(MemoryLayout<sockaddr_un>.size)
        let connected = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, size) == 0
            }
        }
        guard connected else { return false }
        return Array(command.utf8).withUnsafeBufferPointer {
            Darwin.write(fd, $0.baseAddress, $0.count) > 0
        }
    }

    static func stopAll() { send(#"{"cmd": "stop"}"#) }
    static func skipCurrent() { send(#"{"cmd": "skip"}"#) }
}

// --------------------------------------------------------------------------
// 刘海状态条
// --------------------------------------------------------------------------

final class Model: ObservableObject {
    @Published var label = ""
    @Published var snippet = ""
    @Published var queued = 0
    @Published var visible = false
    @Published var speaking = false      // 队列里有货（静音时也算，图标要显示）
}

struct Pill: View {
    @ObservedObject var model: Model

    var body: some View {
        HStack(spacing: 7) {
            Image(systemName: "speaker.wave.2.fill")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.green)
                .symbolEffect(.variableColor.iterative, isActive: model.visible)
            if !model.label.isEmpty {
                Text(model.label)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.white)
            }
            if !model.snippet.isEmpty {
                Text(model.snippet)
                    .font(.system(size: 12))
                    .foregroundStyle(.white.opacity(0.7))
                    .lineLimit(1)
            }
            if model.queued > 0 {
                Text("+\(model.queued)")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(.black)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 1)
                    .background(Capsule().fill(.white.opacity(0.9)))
            }
        }
        .padding(.horizontal, 14)
        .frame(height: 28)
        .frame(maxWidth: 420)
        .fixedSize(horizontal: true, vertical: false)
        .background(
            // 只圆下面两个角，上沿平的，和刘海连成一体
            UnevenRoundedRectangle(cornerRadii: .init(bottomLeading: 14, bottomTrailing: 14))
                .fill(.black)
        )
        .opacity(model.visible ? 1 : 0)
        .scaleEffect(model.visible ? 1 : 0.85, anchor: .top)
        .animation(.spring(duration: 0.3), value: model.visible)
        .animation(.easeInOut(duration: 0.2), value: model.snippet)
    }
}

struct Root: View {
    @ObservedObject var model: Model
    var body: some View { Pill(model: model) }
}

/// 承接点击的容器。SwiftUI 那层没有可交互元素，事件顺着响应链落到这里。
final class ClickContainer: NSView {
    var onClick: (() -> Void)?
    var onRightClick: (() -> Void)?
    override func mouseDown(with event: NSEvent) { onClick?() }
    override func rightMouseDown(with event: NSEvent) { onRightClick?() }
}

// --------------------------------------------------------------------------
// 刘海把手：常驻的一小块，点开就是设置菜单
//
// 为什么不只靠菜单栏图标：菜单栏塞满时（刘海机器上很容易），macOS 会把新图标
// 排到刘海左边那条状态栏里并直接不画出来——NSStatusItem 说自己 visible，
// occlusionState 却是不可见。那等于没有入口。这块把手的位置由我们自己定，
// 一定看得见、点得着。用 AppKit 画：SwiftUI 的点击要穿过 NSHostingView，
// 这里只要一个 mouseDown，不值得为它赌事件链。
// --------------------------------------------------------------------------

final class HandleView: NSView {
    var muted = false { didSet { needsDisplay = true } }
    var onClick: (() -> Void)?
    var onRightClick: (() -> Void)?
    private var hovering = false { didSet { needsDisplay = true } }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        wantsLayer = true
        layer?.masksToBounds = true          // 上沿要切平，和刘海接上
        for area in trackingAreas { removeTrackingArea(area) }
        addTrackingArea(NSTrackingArea(
            rect: bounds,
            options: [.mouseEnteredAndExited, .activeAlways],
            owner: self
        ))
    }

    override func mouseEntered(with event: NSEvent) { hovering = true }
    override func mouseExited(with event: NSEvent) { hovering = false }
    override func mouseDown(with event: NSEvent) { onClick?() }
    override func rightMouseDown(with event: NSEvent) { onRightClick?() }

    override func draw(_ dirty: NSRect) {
        // 平时很淡，鼠标靠近才显出来——它常驻在屏幕顶上，不该抢注意力
        let alpha = hovering ? 0.92 : 0.42
        NSColor.black.withAlphaComponent(alpha).setFill()
        // 圆角矩形往上多画 8pt，被 masksToBounds 切掉，于是只剩下面两个圆角
        let shape = NSRect(x: 0, y: 0, width: bounds.width, height: bounds.height + 8)
        NSBezierPath(roundedRect: shape, xRadius: 8, yRadius: 8).fill()

        let name = muted ? "speaker.slash.fill" : "speaker.wave.2.fill"
        let config = NSImage.SymbolConfiguration(pointSize: 10, weight: .semibold)
        guard let symbol = NSImage(systemSymbolName: name, accessibilityDescription: "语音设置")?
                .withSymbolConfiguration(config) else { return }
        let color = muted ? NSColor.white.withAlphaComponent(hovering ? 0.8 : 0.5)
                          : NSColor.systemGreen.withAlphaComponent(hovering ? 1 : 0.75)
        let size = symbol.size
        let box = NSRect(x: (bounds.width - size.width) / 2,
                         y: (bounds.height - size.height) / 2,
                         width: size.width, height: size.height)
        let tinted = NSImage(size: size, flipped: false) { rect in
            symbol.draw(in: rect)
            color.set()
            rect.fill(using: .sourceAtop)
            return true
        }
        tinted.draw(in: box)
    }
}

// --------------------------------------------------------------------------
// 应用
// --------------------------------------------------------------------------

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let model = Model()
    var panel: NSPanel!
    var pillContainer: ClickContainer!
    var handlePanel: NSPanel!
    var handleView: HandleView!
    var handleMenu: NSMenu!
    var statusItem: NSStatusItem!
    var lastMtime = Date.distantPast
    var lastConfigMtime = Date.distantPast
    var pillHost: NSView?

    // 配置里的开关，菜单打开时刷新一次
    var muted = false
    var ackOnPrompt = true
    var speakNotifications = true
    var announceSession = true

    func applicationDidFinishLaunching(_ note: Notification) {
        setUpPanel()
        setUpHandle()
        setUpStatusItem()
        readConfig(force: true)

        NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil, queue: .main
        ) { [weak self] _ in self?.place() }

        Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            self?.tick()
        }
    }

    // -- 刘海面板 --------------------------------------------------------

    func setUpPanel() {
        panel = NSPanel(
            contentRect: .zero,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.level = .statusBar               // 盖在菜单栏之上，刘海区域本来就是黑的
        panel.ignoresMouseEvents = true        // 没在播的时候完全穿透，绝不挡事
        panel.becomesKeyOnlyIfNeeded = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary,
                                    .stationary, .ignoresCycle]

        // 播报中要能一键闭嘴，所以状态条本身就是按钮：左键出菜单，右键直接静音
        let container = ClickContainer(frame: .zero)
        container.onClick = { [weak self] in self?.popUpMenu(over: container) }
        container.onRightClick = { [weak self] in self?.toggleMute() }
        let host = NSHostingView(rootView: Root(model: model))
        host.autoresizingMask = [.width, .height]
        container.addSubview(host)
        pillContainer = container
        pillHost = host
        panel.contentView = container
        place()
        panel.orderFrontRegardless()
    }

    func setUpHandle() {
        handleView = HandleView(frame: NSRect(x: 0, y: 0, width: HANDLE.width,
                                              height: HANDLE.height))
        handleView.onClick = { [weak self] in
            guard let self else { return }
            self.popUpMenu(over: self.handleView)
        }
        handleView.onRightClick = { [weak self] in self?.toggleMute() }
        handlePanel = NSPanel(
            contentRect: .zero,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: false
        )
        handlePanel.isOpaque = false
        handlePanel.backgroundColor = .clear
        handlePanel.hasShadow = false
        handlePanel.level = .statusBar
        handlePanel.becomesKeyOnlyIfNeeded = true      // 点它不抢走当前窗口的焦点
        handlePanel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary,
                                          .stationary, .ignoresCycle]
        handlePanel.contentView = handleView

        handleMenu = NSMenu()
        handleMenu.delegate = self                     // 和菜单栏图标同一份菜单内容
        place()
        handlePanel.orderFrontRegardless()
    }

    func popUpMenu(over view: NSView) {
        handleMenu.popUp(positioning: nil, at: NSPoint(x: 0, y: 0), in: view)
    }

    /// 挑带刘海的屏幕，把面板贴在刘海正下方；没有刘海就贴菜单栏下沿。
    func place() {
        guard let screen = NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 })
                ?? NSScreen.screens.first else { return }
        let f = screen.frame
        let notch = screen.safeAreaInsets.top
        // 没刘海的机器上 safeAreaInsets 是 0，直接贴屏幕顶会盖住菜单栏本身
        let top = notch > 0 ? notch : NSStatusBar.system.thickness
        // 窗口收到刚好包住状态条：多出来的透明边会吞点击。
        // 尺寸问 NSHostingView 要（fittingSize = SwiftUI 的理想大小），不能让
        // SwiftUI 自己量——窗口从 0 开始，量出来永远是 0，就再也长不起来了。
        let fit = pillHost?.fittingSize ?? .zero
        let w = max(fit.width, HANDLE.width), h = max(fit.height, HANDLE.height)
        panel?.setFrame(
            NSRect(x: f.midX - w / 2, y: f.maxY - top - h, width: w, height: h),
            display: true
        )
        handlePanel?.setFrame(
            NSRect(x: f.midX - HANDLE.width / 2, y: f.maxY - top - HANDLE.height,
                   width: HANDLE.width, height: HANDLE.height),
            display: true
        )
    }

    /// 播报中状态条自己就是按钮，把手让位；空闲时反过来。
    /// 两个总有一个能点——「正在播的时候点不了静音」就是这么来的。
    func updateHandleVisibility() {
        panel?.ignoresMouseEvents = !model.visible   // 看不见时不许吞点击
        guard let handlePanel else { return }
        if model.visible {
            handlePanel.orderOut(nil)
        } else if !handlePanel.isVisible {
            handlePanel.orderFrontRegardless()
        }
    }

    // -- 菜单栏图标 ------------------------------------------------------

    func setUpStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu
        updateIcon()
    }

    func updateIcon() {
        guard let button = statusItem?.button else { return }
        let name = muted ? "speaker.slash.fill"
                 : (model.speaking ? "speaker.wave.2.fill" : "speaker.fill")
        let image = NSImage(systemSymbolName: name, accessibilityDescription: "语音播报")
        image?.isTemplate = true               // 跟随浅色/深色菜单栏
        button.image = image
        button.toolTip = muted ? "语音播报已静音" : "语音播报"
        handleView?.muted = muted
        handleView?.toolTip = muted ? "语音播报已静音（点开设置）" : "语音播报（点开设置）"
    }

    /// 菜单每次打开前重建：静音也可能是命令行改的，勾选状态必须以文件为准。
    func menuNeedsUpdate(_ menu: NSMenu) {
        readConfig(force: true)
        menu.removeAllItems()

        let head = NSMenuItem(title: headline(), action: nil, keyEquivalent: "")
        head.isEnabled = false
        menu.addItem(head)
        menu.addItem(.separator())

        menu.addItem(item("静音（不出声）", #selector(toggleMute), checked: muted))
        menu.addItem(item("跳过当前这段", #selector(skipCurrent)))
        menu.addItem(item("清空队列", #selector(stopAll)))
        menu.addItem(.separator())

        menu.addItem(item("收到指令先应一声", #selector(toggleAck), checked: ackOnPrompt))
        menu.addItem(item("念系统通知和批准请求", #selector(toggleNotifications),
                          checked: speakNotifications))
        menu.addItem(item("换会话时报项目名", #selector(toggleAnnounce),
                          checked: announceSession))
        menu.addItem(.separator())

        menu.addItem(item("打开配置文件…", #selector(openConfig)))
        menu.addItem(item("退出", #selector(quit)))
    }

    private func headline() -> String {
        if muted { return "已静音" }
        if model.speaking {
            let who = model.label.isEmpty ? model.snippet : model.label
            let tail = model.queued > 0 ? "，还有 \(model.queued) 段排队" : ""
            return "正在播报：\(who)\(tail)"
        }
        return "空闲"
    }

    private func item(_ title: String, _ action: Selector, checked: Bool? = nil) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: action, keyEquivalent: "")
        entry.target = self
        if let checked { entry.state = checked ? .on : .off }
        return entry
    }

    // -- 菜单动作 --------------------------------------------------------

    @objc func toggleMute() {
        muted.toggle()
        Config.set("muted", muted)
        if muted {
            // 光写配置得等当前这段播完才安静，点了静音就该立刻安静
            Daemon.stopAll()
            model.visible = false
            updateHandleVisibility()
        }
        lastConfigMtime = Config.mtime()
        updateIcon()
    }

    @objc func toggleAck() { flip("ack_on_prompt", &ackOnPrompt) }
    @objc func toggleNotifications() { flip("speak_notifications", &speakNotifications) }
    @objc func toggleAnnounce() { flip("announce_session", &announceSession) }

    private func flip(_ key: String, _ value: inout Bool) {
        value.toggle()
        Config.set(key, value)
        lastConfigMtime = Config.mtime()
    }

    @objc func skipCurrent() { Daemon.skipCurrent() }
    @objc func stopAll() { Daemon.stopAll() }

    @objc func openConfig() {
        if !FileManager.default.fileExists(atPath: configPath) {
            Config.set("muted", muted)      // 还没有文件就先落一份，否则打不开
        }
        NSWorkspace.shared.open(URL(fileURLWithPath: configPath))
    }

    @objc func quit() { NSApp.terminate(nil) }

    // -- 轮询 ------------------------------------------------------------

    func readConfig(force: Bool) {
        let mtime = Config.mtime()
        guard force || mtime != lastConfigMtime else { return }
        lastConfigMtime = mtime
        let cfg = Config.load()
        muted = Config.flag(cfg, "muted", false)
        ackOnPrompt = Config.flag(cfg, "ack_on_prompt", true)
        speakNotifications = Config.flag(cfg, "speak_notifications", true)
        announceSession = Config.flag(cfg, "announce_session", true)
        if muted { model.visible = false }
        updateIcon()
        updateHandleVisibility()
    }

    func tick() {
        readConfig(force: false)          // 静音也可能是命令行改的
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: statusPath),
              let mtime = attrs[.modificationDate] as? Date else {
            if model.speaking { model.speaking = false; updateIcon() }
            if model.visible { model.visible = false }
            updateHandleVisibility()
            return
        }
        guard mtime != lastMtime else { return }
        lastMtime = mtime

        guard let data = FileManager.default.contents(atPath: statusPath),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return
        }
        let speaking = obj["speaking"] as? [String: Any]
        let queue = obj["queue"] as? [[String: Any]] ?? []

        model.label = (speaking?["label"] as? String) ?? ""
        model.snippet = (speaking?["text"] as? String) ?? ""
        model.queued = queue.count
        let active = speaking != nil || !queue.isEmpty
        model.visible = active && !muted
        DispatchQueue.main.async { self.place() }   // 文字变了，宽度跟着变
        if model.speaking != active {
            model.speaking = active
            updateIcon()
        }
        updateHandleVisibility()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)   // 不上 Dock、不抢焦点，只留菜单栏图标和刘海把手
app.run()
