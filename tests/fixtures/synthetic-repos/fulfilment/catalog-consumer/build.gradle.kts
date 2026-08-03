// OI-3 regression fixture: no coordinate string appears anywhere in this file,
// so the inline-coordinate regex finds nothing and dependencies_internal was [].
dependencies {
    implementation(libs.warehouseServiceClient)
}
