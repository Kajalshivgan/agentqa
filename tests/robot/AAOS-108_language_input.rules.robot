*** Settings ***
Documentation     Recorded flow 'AAOS-108_language_input' (rule-based conversion).
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
AAOS 108 Language Input
    Restart App    com.android.car.carlauncher
    Tap On Description    Applications
    Assert Visible    Settings
    Tap On Text    Settings
    Assert Visible    Connected devices
