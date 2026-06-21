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
        versionCode = 1
        versionName = "0.2.0"

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

chaquopy {
    defaultConfig {
        buildPython("/usr/bin/python3")
        pip {
            install("rns")
            install("aiohttp")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir("../../chatxz")
        }
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.12.1")
}
