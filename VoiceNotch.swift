// VoiceNotch — 灵动岛式的语音播报状态条，吸附在 MacBook 刘海下沿。
//
// 谁在说话、后面还排着几段，听得到也看得到。数据来自 tts_daemon.py
// 写的状态文件，本程序只读不写，250ms 轮询一次（mtime 不变就跳过）。
//
// 编译（install.py 会自动做）：
//   swiftc -O VoiceNotch.swift -o ~/.claude/hooks/voice-notch

import AppKit
import SwiftUI

let statusPath = NSString(string: "~/.claude/.voice_status.json").expandingTildeInPath

final class Model: ObservableObject {
    @Published var label = ""
    @Published var snippet = ""
    @Published var queued = 0
    @Published var visible = false
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
    var body: some View {
        VStack(spacing: 0) {
            Pill(model: model)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    let model = Model()
    var panel: NSPanel!
    var lastMtime = Date.distantPast

    func applicationDidFinishLaunching(_ note: Notification) {
        panel = NSPanel(
            contentRect: .zero,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.level = .statusBar               // 盖在菜单栏之上，刘海区域本来就是黑的
        panel.ignoresMouseEvents = true        // 完全点击穿透，绝不挡事
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary,
                                    .stationary, .ignoresCycle]
        panel.contentView = NSHostingView(rootView: Root(model: model))
        place()
        panel.orderFrontRegardless()

        NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil, queue: .main
        ) { [weak self] _ in self?.place() }

        Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            self?.tick()
        }
    }

    /// 挑带刘海的屏幕，把面板贴在刘海正下方；没有刘海就贴屏幕顶。
    func place() {
        guard let screen = NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 })
                ?? NSScreen.screens.first else { return }
        let f = screen.frame
        let notch = screen.safeAreaInsets.top
        let w: CGFloat = 640, h: CGFloat = 60
        panel.setFrame(
            NSRect(x: f.midX - w / 2, y: f.maxY - notch - h, width: w, height: h),
            display: true
        )
    }

    func tick() {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: statusPath),
              let mtime = attrs[.modificationDate] as? Date else {
            if model.visible { model.visible = false }
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
        model.visible = speaking != nil || !queue.isEmpty
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)   // 不上 Dock、不抢焦点
app.run()
