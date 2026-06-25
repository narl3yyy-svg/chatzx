import java.util.Properties
import java.io.FileInputStream

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

val versionProps = Properties().apply {
    val file = rootProject.file("../version.properties")
    if (file.exists()) {
        load(FileInputStream(file))
    }
}
val appVersionName = versionProps.getProperty("VERSION_NAME", "0.0.0")
val appVersionCode = versionProps.getProperty("VERSION_CODE", "1").toInt()
// CI release metadata (keep in sync via scripts/bump-version.sh)
val releaseVersionNameForCi = "0.3.121"  // versionName
val releaseVersionCodeForCi = 119  // versionCode

android {
    namespace = "com.chatxz.android"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.chatxz.android"
        minSdk = 26
        targetSdk = 34
        versionCode = appVersionCode
        versionName = appVersionName

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("debug")
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        resources {
            excludes += setOf("META-INF/DEPENDENCIES", "META-INF/LICENSE", "META-INF/NOTICE")
        }
    }
}

configurations.all {
    exclude(mapOf("group" to "org.jetbrains.kotlin", "module" to "kotlin-stdlib-jdk8"))
    exclude(mapOf("group" to "org.jetbrains.kotlin", "module" to "kotlin-stdlib-jdk7"))
}

chaquopy {
    defaultConfig {
        version = "3.13"
        pip {
            install("cryptography>=41.0.0")
            install("../deps/pyserial-3.5.tar.gz")
            install("../deps/usbserial4a-0.4.0.tar.gz")
            install("../deps/rns-1.3.5.tar.gz")
            install("aiohttp")
        }
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.12.1")
}
