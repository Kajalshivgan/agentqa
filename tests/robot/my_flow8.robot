*** Settings ***
Documentation     Verifies navigating from the app grid through Settings into Display and Sound settings and back to the app grid.
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
My Flow8
    Restart App    com.android.car.carlauncher
    Tap On Text    Settings
    Assert Visible    Connected devices
    Tap On Text    Display
    Tap On Text    Sound
    Assert Visible    Media volume
    Tap On Description    Applications
    Assert Visible    Appium Settings
