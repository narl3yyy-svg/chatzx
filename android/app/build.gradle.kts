plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

android {
    namespace = "com.chatzx.android"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.chatzx.android"
        minSdk = 26
        targetSdk = 34
        versionCode = 10
        versionName = "0.3.9"

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
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
            install("../deps/rns-1.3.5.tar.gz")
            install("aiohttp")
        }
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.12.1")
}
