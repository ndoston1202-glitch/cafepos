#!/usr/bin/env bash
# CafePOS Android ilovasini (APK) Gradle'siz, to'g'ridan-to'g'ri Android SDK vositalari bilan yig'adi.
# GitHub Actions'da ishlaydi (u yerda Android SDK tayyor). Natija: android/build/CafePOS.apk
set -euo pipefail
cd "$(dirname "$0")"

SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
[ -n "$SDK" ] || { echo "ANDROID_HOME topilmadi"; exit 1; }
BT="$(ls -d "$SDK"/build-tools/* | sort -V | tail -1)"
PLATFORM="$SDK/platforms/android-34"
[ -f "$PLATFORM/android.jar" ] || PLATFORM="$(ls -d "$SDK"/platforms/android-* | sort -V | tail -1)"
ANDROID_JAR="$PLATFORM/android.jar"
VERSION_CODE="${VERSION_CODE:-1}"
VERSION_NAME="${VERSION_NAME:-1.0.$VERSION_CODE}"
echo "build-tools: $BT"
echo "platform:    $PLATFORM"

OUT=build
rm -rf "$OUT"
mkdir -p "$OUT/classes"

# 1) Resurslar (ikonkalar, logo) va manifest
"$BT/aapt2" compile --dir res -o "$OUT/res.zip"
"$BT/aapt2" link -o "$OUT/base.apk" -I "$ANDROID_JAR" --manifest AndroidManifest.xml \
  --min-sdk-version 21 --target-sdk-version 34 \
  --version-code "$VERSION_CODE" --version-name "$VERSION_NAME" "$OUT/res.zip"

# 2) Java -> .class -> classes.dex
javac --release 8 -classpath "$ANDROID_JAR" -d "$OUT/classes" $(find src -name '*.java')
"$BT/d8" --release --lib "$ANDROID_JAR" --min-api 21 --output "$OUT" $(find "$OUT/classes" -name '*.class')

# 3) APK: dex qo'shish, tekislash, imzolash
cp "$OUT/base.apk" "$OUT/unsigned.apk"
(cd "$OUT" && zip -q unsigned.apk classes.dex)
"$BT/zipalign" -f -p 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
"$BT/apksigner" sign --ks cafepos.keystore --ks-key-alias cafepos \
  --ks-pass "pass:${KEYSTORE_PASSWORD:-cafepos2026}" --key-pass "pass:${KEYSTORE_PASSWORD:-cafepos2026}" \
  --out "$OUT/CafePOS.apk" "$OUT/aligned.apk"
"$BT/apksigner" verify "$OUT/CafePOS.apk"
ls -la "$OUT/CafePOS.apk"
