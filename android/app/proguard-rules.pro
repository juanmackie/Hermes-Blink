# UnifiedPush calls these overrides through its service callback contract. Keep only
# the declared entry points, while still allowing R8 to optimize their implementations.
-keep,allowoptimization class com.you.hermeswidget.push.UnifiedPushService {
    public <init>();
    public void onNewEndpoint(org.unifiedpush.android.connector.data.PushEndpoint, java.lang.String);
    public void onMessage(org.unifiedpush.android.connector.data.PushMessage, java.lang.String);
    public void onRegistrationFailed(org.unifiedpush.android.connector.FailedReason, java.lang.String);
    public void onUnregistered(java.lang.String);
}

# Tink's Android binary references these types only from annotations. They are not
# needed at runtime and are omitted from the current Android dependency graph.
-dontwarn javax.annotation.Nullable
-dontwarn javax.annotation.concurrent.GuardedBy

# Glance's generated-protobuf reflection keep rules are packaged by glance-appwidget.
# AndroidSVG is called through its typed API, so it does not need a package-wide keep rule.
