*** Settings ***
Documentation     Recorded flow 'my_flow8' (rule-based conversion).
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
My Flow8
    Restart App    com.android.car.carlauncher
    Tap On Text    Settings
    Assert Visible    Display
    Tap On Text    Display
    Tap On Text    Sound
    Assert Visible    Play media on
    Tap On Description    Applications
    Assert Visible    Appium Settings
