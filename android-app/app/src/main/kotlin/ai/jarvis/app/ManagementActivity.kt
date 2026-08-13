package ai.jarvis.app

import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.config.ServerKind
import ai.jarvis.app.config.Origin
import ai.jarvis.app.config.ServerUrl
import ai.jarvis.app.ui.ConsoleFrame
import ai.jarvis.app.ui.ConsoleTab
import ai.jarvis.app.ui.JarvisUi
import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.GeolocationPermissions
import android.webkit.HttpAuthHandler
import android.webkit.PermissionRequest
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import java.io.ByteArrayInputStream

/**
 * A window onto the jarvis-web console, pinned to that one origin.
 *
 * **The console is jarvis-web, not jarvis-core.** This comment used to say
 * otherwise, and that belief is what made voice work here and nowhere else:
 * everything else in the app assumed the configured URL was jarvis-core and
 * dialled its socket path, while this screen quietly worked because a web page
 * talking to its own relay is the one case that does. See [ServerKind].
 *
 * The WebView is the one place in this app where remote content executes, so it
 * is fenced in:
 *
 *  * **One origin, enforced twice.** [WebViewClient.shouldOverrideUrlLoading]
 *    blocks navigation elsewhere, and [WebViewClient.shouldInterceptRequest]
 *    blocks sub-resources too — iframes and XHR never reach the navigation
 *    callback, so origin-checking there alone would be theatre.
 *  * **No bridge.** `addJavascriptInterface` is never called. There is no path
 *    from page JavaScript into the action dispatcher: whatever the page does,
 *    it does inside the WebView, and a Tier-3 action still has to go through
 *    [ai.jarvis.app.ui.ApprovalBridge] and a human.
 *  * **No file access.** `allowFileAccess`/`allowContentAccess` are off, so the
 *    file-URL escalation settings are moot and are left at their `false`
 *    defaults rather than being touched at all.
 *  * **No mixed content, no third-party cookies, no geolocation, no camera or
 *    mic** — the page gets none of them.
 *
 * The bearer token rides an `Authorization` header on every navigation this
 * *app* starts — the first load and each tab switch. That is all the platform
 * offers: WebView does not attach `additionalHeaders` to sub-resources or to
 * navigations the page itself initiates, so following a link inside the console
 * carries nothing. The console is expected to turn the first authenticated
 * request into a session of its own; the tab strip exists partly because of
 * this, since a link tap in the page's own nav is exactly the navigation the
 * header does not reach.
 *
 * **The section comes from [ConsoleTab], never from the intent.** See
 * [EXTRA_TAB]: an authenticated WebView is not something any component on the
 * device that can start an activity should be able to aim.
 */
class ManagementActivity : Activity() {

    private lateinit var config: JarvisConfig
    private var webView: WebView? = null
    private var serverOrigin: Origin? = null
    private var tab: ConsoleTab = ConsoleTab.DEFAULT
    /** Holds the tab strip, so marking the current tab can rebuild it. */
    private var tabSlot: FrameLayout? = null

    /**
     * Jarvis's own "loading" and "that did not work", drawn over the WebView.
     *
     * This screen had neither, and the result was the worst-looking failure in
     * the app: an unreachable console rendered **Chromium's** white
     * "webpage not available" page — system fonts, a Chrome error code, a
     * RELOAD button that is not this app's — full-bleed inside an all-black
     * Jarvis, while the tab strip above it still highlighted a tab it had never
     * loaded. There was no `onReceivedError` override, no
     * `onReceivedHttpError`, and no progress indicator of any kind, so a slow
     * console was indistinguishable from a dead one for as long as it took.
     */
    private var statusPanel: LinearLayout? = null
    private var statusTitle: TextView? = null
    private var statusDetail: TextView? = null
    private var statusRetry: android.widget.Button? = null

    /**
     * Set when the current navigation failed, so [onPageFinished] does not
     * clear the error panel it is about to be told about.
     *
     * The platform calls `onReceivedError` BEFORE `onPageFinished` for a failed
     * main frame, and hiding the panel on "finished" would flash the error and
     * then show Chromium's page underneath it anyway.
     */
    private var failed = false

    /**
     * Set while an app-initiated navigation is in flight. See [onBackPressed].
     */
    private var resettingHistory = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        config = JarvisConfig(this)

        val url = config.serverUrl
        val origin = ServerUrl.originOf(url)
        if (url.isEmpty() || origin == null) {
            Toast.makeText(this, "Set a valid server URL first", Toast.LENGTH_SHORT).show()
            startActivity(Intent(this, SettingsActivity::class.java))
            finish()
            return
        }
        serverOrigin = origin

        // Only the jarvis-web console has a management page. Pointed at
        // jarvis-core the WebView would load the API's JSON index, which looks
        // like a broken app rather than like a URL aimed at the wrong one of
        // two servers — so say which it is. `null` means the voice path has not
        // discovered it yet, and guessing wrong here would send someone to
        // Settings to "fix" a URL that was right.
        if (config.serverKind == ServerKind.CORE) {
            Toast.makeText(
                this,
                "That address is jarvis-core, which has no management page. " +
                    "Point Jarvis at the web console to manage from here.",
                Toast.LENGTH_LONG,
            ).show()
            startActivity(Intent(this, SettingsActivity::class.java))
            finish()
            return
        }

        // The NAME of a tab, never a path. See ConsoleTab: this WebView carries
        // the user's bearer token, so a caller-supplied URL would be a way for
        // anything on the device that can start an activity to point an
        // authenticated session wherever it liked.
        tab = ConsoleTab.of(intent?.getStringExtra(EXTRA_TAB))

        val view = WebView(this)
        webView = view
        configure(view)
        setContentView(buildUi(view).also { JarvisUi.fitSystemBars(it) })
        load(tab)
    }

    /**
     * Open one of the console's sections.
     *
     * `loadUrl` with headers, not `WebView.loadUrl(url)`: the platform attaches
     * `additionalHeaders` to the navigation it is given and to nothing else, so
     * every top-level navigation this app initiates has to carry the bearer
     * itself. Following a link inside the page does not, which is why the
     * console is expected to turn the first authenticated request into a
     * session.
     */
    private fun load(next: ConsoleTab) {
        tab = next
        resettingHistory = true
        failed = false
        showLoading(next)
        val base = config.serverUrl.trimEnd('/')
        webView?.loadUrl(base + next.path, mapOf("Authorization" to "Bearer ${config.token}"))
        markCurrentTab()
    }

    // --- loading and failure ------------------------------------------------

    /**
     * Say which section is being fetched, in Jarvis's own type.
     *
     * Named rather than a bare spinner: the tab strip highlights the
     * destination the instant it is tapped, so without this the only difference
     * between "Tools is loading" and "Tools is blank" is that one of them
     * eventually changes.
     */
    private fun showLoading(next: ConsoleTab) {
        statusTitle?.text = "LOADING ${next.label.uppercase()}"
        statusDetail?.text = serverOrigin?.host.orEmpty()
        statusRetry?.visibility = android.view.View.GONE
        statusPanel?.visibility = android.view.View.VISIBLE
    }

    private fun hideStatus() {
        statusPanel?.visibility = android.view.View.GONE
    }

    /**
     * Replace Chromium's error page with one that says something useful.
     *
     * The WebView is blanked first: the platform has already rendered its own
     * error document into it by the time this is called, and a panel drawn over
     * the top would still be sitting on a white page — visible around the edges
     * and behind every scroll.
     *
     * `loadData` with an empty document, never `loadUrl("about:blank")`. Every
     * `loadUrl` from this activity carries the bearer header, and
     * `console_parity_test.py` enforces that because a navigation without it
     * lands on the console's login page inside a WebView nobody can type into.
     * Clearing the document is not a navigation and should not look like one.
     */
    private fun showError(title: String, detail: String) {
        failed = true
        webView?.loadData("", "text/html", "utf-8")
        statusTitle?.text = title
        statusDetail?.text = detail
        statusRetry?.visibility = android.view.View.VISIBLE
        statusPanel?.visibility = android.view.View.VISIBLE
    }

    private fun buildUi(view: WebView): ViewGroup {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(JarvisUi.BG)
        }

        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            val p = JarvisUi.dp(this@ManagementActivity, 12)
            setPadding(p, p, p, p)
        }
        bar.addView(
            TextView(this).apply {
                text = serverOrigin?.host ?: ""
                setTextColor(JarvisUi.ACCENT)
                textSize = 12f
                letterSpacing = 0.16f
                typeface = android.graphics.Typeface.MONOSPACE
            },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )
        bar.addView(JarvisUi.ghost(this, "RELOAD") { reload() })
        root.addView(
            bar,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )

        // The console's own nav, on the phone.
        //
        // Without it this screen was the console's front door and nothing else:
        // reaching Tools meant going back to the home screen and starting again,
        // because the page's own nav is inside a WebView whose links do not
        // carry the bearer header. Switching here re-issues an authenticated
        // navigation, which is the only kind that works.
        val slot = FrameLayout(this)
        tabSlot = slot
        slot.addView(
            tabBar(),
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )
        root.addView(
            slot,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )

        // The WebView and Jarvis's own status panel share the remaining space,
        // stacked. A FrameLayout rather than swapping views in and out: the
        // WebView keeps its state and its scroll position across a failed
        // reload, and the panel is one `visibility` away in either direction.
        val body = FrameLayout(this)
        body.addView(
            view,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        body.addView(
            buildStatusPanel(),
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        root.addView(
            body,
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f)
        )
        return root
    }

    /**
     * The panel that stands in for Chromium's white page.
     *
     * Opaque, on Jarvis's own ground, and clickable so it swallows taps: a
     * transparent overlay would let the user interact with an error document
     * underneath it, and a half-loaded console is exactly the thing not to be
     * tapping at.
     */
    private fun buildStatusPanel(): LinearLayout {
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(JarvisUi.BG)
            isClickable = true
            val p = JarvisUi.dp(this@ManagementActivity, JarvisUi.Space.SCREEN)
            setPadding(p, p, p, p)
            visibility = android.view.View.GONE
        }
        statusTitle = TextView(this).apply {
            setTextColor(JarvisUi.ACCENT)
            textSize = JarvisUi.Type.LABEL
            letterSpacing = 0.2f
            typeface = android.graphics.Typeface.create(
                android.graphics.Typeface.MONOSPACE,
                android.graphics.Typeface.BOLD,
            )
            gravity = Gravity.CENTER
            // Read out when it changes: this is the only thing on screen that
            // says whether the console arrived, and a blank black rectangle
            // announces nothing on its own.
            JarvisUi.liveRegion(this)
        }
        panel.addView(statusTitle)
        statusDetail = JarvisUi.hint(this, "").apply { gravity = Gravity.CENTER }
        panel.addView(statusDetail)
        statusRetry = JarvisUi.ghost(this, "TRY AGAIN") { reload() }.apply {
            visibility = android.view.View.GONE
        }
        panel.addView(
            statusRetry,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = JarvisUi.dp(this@ManagementActivity, JarvisUi.Space.GAP) }
        )
        statusPanel = panel
        return panel
    }

    /**
     * The tab strip, matching the console's own and in the console's order —
     * plus PHONE, which is this handset rather than the house. Built by
     * [ConsoleFrame] because the settings screen shows the same strip, and two
     * copies of one nav is what this change exists to stop.
     */
    private fun tabBar(): ViewGroup =
        ConsoleFrame.tabBar(this, current = tab, onPhone = false) { load(it) }

    /**
     * Which tab you are on, said in the one way a ghost button can say it.
     *
     * The strip is rebuilt rather than repainted: it is seven small views, it
     * is rebuilt only on a tab switch, and the alternative was this class
     * keeping a parallel list of buttons in step with a strip built somewhere
     * else — which is the bookkeeping that made two copies of the nav drift in
     * the first place.
     */
    private fun markCurrentTab() {
        val slot = tabSlot ?: return
        slot.removeAllViews()
        slot.addView(
            tabBar(),
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )
    }

    private fun reload() {
        // Re-issue the authenticated navigation rather than WebView.reload(),
        // which would replay the last one without the header — and re-issue it
        // for the tab the user is actually on, not for the console's root.
        load(tab)
    }

    private fun configure(view: WebView) {
        if (BuildConfig.DEBUG) {
            WebView.setWebContentsDebuggingEnabled(true)
        }

        view.settings.apply {
            // The management UI is a real web app; it needs these two.
            javaScriptEnabled = true
            domStorageEnabled = true

            // Everything else is denied.
            allowFileAccess = false
            allowContentAccess = false
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            // Jarvis speaks its replies. With a gesture requirement the browser
            // HUD's WebAudio playback stays suspended and every answer is
            // silent, which for a voice assistant is the same as broken. The
            // page can still only reach the mic through onPermissionRequest
            // below, so relaxing autoplay does not widen what it can capture.
            mediaPlaybackRequiresUserGesture = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setGeolocationEnabled(false)
            builtInZoomControls = true
            displayZoomControls = false
            userAgentString = USER_AGENT
        }

        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setAcceptThirdPartyCookies(view, false)
        }

        view.isVerticalScrollBarEnabled = true
        view.setBackgroundColor(JarvisUi.BG)
        view.webViewClient = originLockedClient()
        view.webChromeClient = lockedChromeClient()
        // Deliberately absent: view.addJavascriptInterface(...). Do not add one.
    }

    /**
     * Tell the page it is inside this app, from this side of the WebView.
     *
     * The console hides its own nav when `<html data-embed="android">` is set,
     * because a link tapped inside a WebView is a page-initiated navigation and
     * WebView does not attach `additionalHeaders` to those — so the page's copy
     * of the nav is a row of tabs that looks right and silently cannot carry
     * the bearer token. The native strip is the only nav in this app that works.
     *
     * That attribute is set server-side, by sniffing this activity's
     * User-Agent in `jarvis-web/src/hooks.server.ts`. It is the right place for
     * it — it lands before the first paint, so there is no flash of a nav that
     * is about to vanish — and it has exactly one failure mode: it is the
     * SERVER's job, so a console that has not been updated alongside the app,
     * or a reverse proxy that normalises the User-Agent, silently leaves the
     * duplicate row on screen. Reported twice as "still the duplicate tabs".
     *
     * So the app asserts it too. Setting an attribute is not a style and not an
     * inline script — it makes the page's OWN stylesheet rule match, which is
     * already loaded and already allowed by its `style-src 'self'`. Belt and
     * braces, and the braces cost one evaluateJavascript per page.
     */
    private fun markEmbedded(view: WebView) {
        view.evaluateJavascript(
            "document.documentElement.setAttribute('data-embed','android')",
            null,
        )
    }

    private fun originLockedClient() = object : WebViewClient() {

        override fun onPageFinished(view: WebView, url: String) {
            // `failed` rather than the url. The platform calls onReceivedError
            // BEFORE onPageFinished for a failed main frame, and it calls
            // onPageFinished again for the empty document showError loads to
            // clear Chromium's own error page — so "finished" arrives twice for
            // one failure, and taking the panel down on either would flash the
            // message and then show nothing.
            if (!failed) hideStatus()
            markEmbedded(view)
            if (!resettingHistory) return
            resettingHistory = false
            // A tab switch must not leave a back-forward entry.
            //
            // Every navigation this app starts carries the bearer header, and
            // `goBack()` re-issues the entry WITHOUT it — so back after two tab
            // switches would land on whatever the console serves an
            // unauthenticated request, inside a WebView, with the tab strip
            // still saying you were somewhere else. Clearing here leaves back
            // meaning what it should: walk this section's own history (the
            // console navigates itself with pushState, and those entries
            // restore with no request at all), then leave.
            view.clearHistory()
        }

        override fun shouldOverrideUrlLoading(
            view: WebView,
            request: WebResourceRequest,
        ): Boolean {
            if (isAllowed(request.url)) return false
            blocked(request.url)
            return true
        }

        override fun shouldInterceptRequest(
            view: WebView,
            request: WebResourceRequest,
        ): WebResourceResponse? {
            if (isAllowed(request.url)) return null
            // Empty 200 rather than an error page: a blocked tracker or CDN
            // font should not turn into a broken-looking management UI. A fresh
            // stream each call — a shared one would be exhausted after the first.
            return WebResourceResponse("text/plain", "utf-8", ByteArrayInputStream(ByteArray(0)))
        }

        /**
         * The console could not be reached at all — DNS, refused, timed out,
         * the server off.
         *
         * `request.isForMainFrame` is the whole guard. This fires for every
         * failed sub-resource too, including the ones
         * [shouldInterceptRequest] deliberately blocks, and turning a blocked
         * tracker into a full-screen "cannot reach your server" would be a
         * worse lie than Chromium's page.
         */
        override fun onReceivedError(
            view: WebView,
            request: WebResourceRequest,
            error: WebResourceError,
        ) {
            if (!request.isForMainFrame) return
            val detail = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                error.description?.toString().orEmpty()
            } else {
                ""
            }
            showError(
                "CANNOT REACH THE CONSOLE",
                "${serverOrigin?.host ?: "That address"} did not answer" +
                    (if (detail.isEmpty()) "." else ": $detail.") +
                    "\n\nCheck the server is running and that this phone is on the " +
                    "same network — or on the VPN — then try again."
            )
        }

        /**
         * It answered, and said no.
         *
         * Kept apart from [onReceivedError] because the two send the user to
         * completely different places: one is a network to fix, the other is a
         * token to re-pair. 401/403 is the common one — a WebView navigation
         * carries the bearer only on the requests this app starts, and an
         * expired token looks exactly like a broken console otherwise.
         */
        override fun onReceivedHttpError(
            view: WebView,
            request: WebResourceRequest,
            response: WebResourceResponse,
        ) {
            if (!request.isForMainFrame) return
            val code = response.statusCode
            showError(
                "THE CONSOLE ANSWERED $code",
                when (code) {
                    401, 403 ->
                        "Your server refused this phone's access token. Re-pair the " +
                            "phone from Settings — PASTE or SCAN QR."

                    404 ->
                        "That address has no ${tab.label.lowercase()} page. It may be " +
                            "jarvis-core rather than the web console: jarvis-core is the " +
                            "API and has no management UI."

                    else -> "Something went wrong on your server. Its logs will say what."
                }
            )
        }

        override fun onReceivedSslError(
            view: WebView,
            handler: SslErrorHandler,
            error: SslError,
        ) {
            // Never proceed. A private CA belongs in the user trust anchors of
            // res/xml/network_security_config.xml, not in a "continue anyway".
            handler.cancel()
            // A toast alone was the whole report, and a cancelled navigation
            // leaves the WebView showing Chromium's error page underneath it —
            // so the panel says the same thing where it will still be readable
            // in ten seconds.
            showError(
                "TLS ERROR",
                "The certificate your server presented was not trusted, so nothing " +
                    "was loaded. A private CA belongs in this app's network security " +
                    "config, never in a “continue anyway”."
            )
        }

        override fun onReceivedHttpAuthRequest(
            view: WebView,
            handler: HttpAuthHandler,
            host: String?,
            realm: String?,
        ) {
            handler.cancel()
        }
    }

    private fun lockedChromeClient() = object : WebChromeClient() {

        override fun onPermissionRequest(request: PermissionRequest) {
            // The page may use the MICROPHONE — and nothing else — when it is
            // the user's own jarvis-web console (same origin as the pinned
            // server) and the app itself already holds RECORD_AUDIO. That is the
            // browser HUD's push-to-talk, running inside the app the user
            // pointed at their own server. Denying it outright was the reason
            // the in-app voice button did nothing.
            //
            // Note the getUserMedia constraint the platform imposes on top of
            // this: `navigator.mediaDevices` only exists in a secure context, so
            // the mic works here over https or over http to localhost, but not
            // over plain http to a LAN IP. A cleartext LAN server needs TLS (see
            // the mkcert note in jarvis-web) for in-WebView voice to reach the
            // page at all — this grant is necessary but not on its own
            // sufficient.
            //
            // The camera is never granted. A request that bundles it in is
            // refused whole rather than partially satisfied: a management
            // console has no business with the camera, and a mixed request is
            // not one to reason about resource by resource.
            val wantsCamera = request.resources.any {
                it == PermissionRequest.RESOURCE_VIDEO_CAPTURE
            }
            val wantsAudio = request.resources.any {
                it == PermissionRequest.RESOURCE_AUDIO_CAPTURE
            }
            // Into a local: `request.origin` is a Java getter, so reading it
            // twice is two calls and Kotlin will not smart-cast the platform
            // type between them.
            val origin = request.origin
            val sameOrigin = origin != null && isAllowed(origin)
            val appHasMic = checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED

            if (wantsAudio && !wantsCamera && sameOrigin && appHasMic) {
                request.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE))
            } else {
                request.deny()
            }
        }

        override fun onGeolocationPermissionsShowPrompt(
            origin: String?,
            callback: GeolocationPermissions.Callback?,
        ) {
            callback?.invoke(origin, false, false)
        }
    }

    /**
     * Same scheme, host and port as the configured server, and nothing else.
     *
     * Compared component-by-component off the [Uri] the WebView handed us
     * rather than by re-parsing its string form: a sub-resource URL with an
     * unescaped character parses fine here but would throw in a strict
     * java.net.URI, and "the parser choked" must not become "allowed".
     */
    private fun isAllowed(uri: Uri): Boolean {
        val expected = serverOrigin ?: return false
        val scheme = uri.scheme?.lowercase() ?: return false
        if (scheme != "http" && scheme != "https") return false
        val host = uri.host?.lowercase() ?: return false
        val port = when {
            uri.port >= 0 -> uri.port
            scheme == "https" -> 443
            else -> 80
        }
        return scheme == expected.scheme && host == expected.host && port == expected.port
    }

    private fun blocked(uri: Uri) {
        toast("Blocked: ${uri.host ?: uri.scheme} is not your Jarvis server")
    }

    private fun toast(message: String) =
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    @Deprecated("Predictive back is disabled in the manifest, so this is the back path.")
    override fun onBackPressed() {
        val view = webView
        if (view != null && view.canGoBack()) {
            view.goBack()
            return
        }
        @Suppress("DEPRECATION")
        super.onBackPressed()
    }

    override fun onDestroy() {
        webView?.let { view ->
            view.stopLoading()
            (view.parent as? ViewGroup)?.removeView(view)
            view.webChromeClient = null
            view.destroy()
        }
        webView = null
        super.onDestroy()
    }

    companion object {
        /**
         * Identifies this app to jarvis-core without advertising the device
         * model, the Android version, or anything else a fingerprinter enjoys.
         */
        private val USER_AGENT =
            "JarvisAndroid/${BuildConfig.VERSION_NAME} (ai.jarvis.app; management)"

        /**
         * Which section to open, as a [ConsoleTab] NAME.
         *
         * A name and not a path, and not a URL. This activity loads what it is
         * given into a WebView carrying the user's bearer token, so accepting a
         * path here would let anything on the device that can start an activity
         * aim an authenticated session wherever it liked. The path comes from
         * [ConsoleTab] and an unrecognised name resolves to the default.
         */
        const val EXTRA_TAB = "ai.jarvis.app.CONSOLE_TAB"

        /** Open the console at [tab]. */
        fun intent(context: android.content.Context, tab: ConsoleTab): Intent =
            Intent(context, ManagementActivity::class.java).putExtra(EXTRA_TAB, tab.name)
    }
}
