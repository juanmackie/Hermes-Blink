package com.you.hermeswidget.net

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The client must work against the plugin that is *deployed*, not the one in this tree.
 *
 * Round 13 constraint: the release may not require a server deployment. The 0.4.6 client
 * adds one wire field, `source`, and that is only safe because the deployed route
 * (`server.py::_widget_events`, commit 635b0e8) reads a fixed list of envelope fields and
 * ignores anything else. Verified against the deployed module, not reasoned about: the
 * pill's exact payload returned 200, wrote the event and the update-request row, recorded
 * no rejection, and the field was absent from the stored payload.
 *
 * That is a property of *the client's* payload, so it is pinned here. A future field
 * outside the allow-list below is the thing that would need a server deployment, and this
 * test is where that has to be noticed.
 */
class DeployedContractCompatibilityTest {

    /**
     * Fields the deployed event route copies from the envelope into the stored payload
     * (server.py at 635b0e8), plus the two envelope fields it always reads itself.
     * Anything else the client sends is ignored there — safe, but unrecorded.
     */
    private val deployedKnownFields = setOf(
        "event", "payload", "clientEventId", "itemId", "actionClass",
        "confirmOnDevice", "revision", "instanceId",
    )

    /** The one additive field, inert until the server that stores it is deployed. */
    private val additiveFields = setOf("source")

    private fun read(path: String): String {
        var dir: File? = File(System.getProperty("user.dir") ?: ".").absoluteFile
        repeat(4) {
            val base = dir?.resolve("src/main/java/com/you/hermeswidget")
            for (pkg in listOf("net", "widget", "")) {
                val candidate = base?.resolve(pkg)?.resolve(path)
                if (candidate != null && candidate.isFile) return candidate.readText()
            }
            dir = dir?.parentFile
        }
        throw AssertionError("could not locate $path")
    }

    private fun keysOf(builder: org.json.JSONObject): Set<String> =
        builder.keys().asSequence().toList().toSet()

    @Test
    fun `both request_update bodies stay inside the deployed contract`() {
        for (body in listOf(
            RequestUpdateEvent.widgetBody("40"),
            RequestUpdateEvent.inAppBody("40"),
        )) {
            val keys = keysOf(body)
            val unknown = keys - deployedKnownFields - additiveFields
            assertTrue(
                "these fields are unknown to the deployed server and would be dropped " +
                    "there: $unknown",
                unknown.isEmpty(),
            )
        }
    }

    @Test
    fun `the additive field is only the one we declared`() {
        // If a second additive field appears, the deployed server drops it too and the
        // attribution silently stops working. That is the moment to know, not later.
        val widget = keysOf(RequestUpdateEvent.widgetBody("40"))
        val inApp = keysOf(RequestUpdateEvent.inAppBody("40"))
        assertTrue(
            "widget body should carry clientEventId, instanceId and source: $widget",
            widget.containsAll(setOf("clientEventId", "source")),
        )
        assertTrue(
            "in-app body should mirror the widget body: $inApp vs $widget",
            inApp == widget,
        )
        assertTrue(
            "the only field the deployed server does not know is `source`, not " +
                "${widget - deployedKnownFields - additiveFields}",
            widget - deployedKnownFields == setOf("source"),
        )
    }

    @Test
    fun `the widget action path adds nothing outside the contract`() {
        val callbacks = read("ActionCallbacks.kt")
            .replace(Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL), "")
            .lines().filterNot { it.trimStart().startsWith("//") }
            .joinToString("\n")
            .let { Regex("""//.*""").replace(it, "") }
        val allowed = setOf("payload", "clientEventId", "itemId", "instanceId", "source")
        val put = Regex("""body\.put\("([a-zA-Z]+)"""").findAll(callbacks)
            .map { it.groupValues[1] }
            .toSet()
        assertTrue(
            "the callback body writes fields the deployed server does not know: " +
                "${put - allowed}",
            (put - allowed).isEmpty(),
        )
    }
}
