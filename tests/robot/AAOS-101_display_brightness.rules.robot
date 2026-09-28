*** Settings ***
Documentation     Recorded flow 'AAOS-101_display_brightness' (rule-based conversion).
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
AAOS 101 Display Brightness
    Restart App    com.android.car.carlauncher
    Tap On Text    Settings
    Assert Visible    Display
    Tap On Text    Display
    Assert Visible    Brightness level
    Tap On Text    Sound
    Assert Visible    Play media on
    Tap On Description    Applications
    Assert Visible    Appium Settings
