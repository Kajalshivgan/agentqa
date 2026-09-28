*** Settings ***
Documentation     Verifies navigating from the car launcher to Display settings and adjusting the brightness level.
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
My Flow
    Restart App    com.android.car.carlauncher
    Tap On Description    Applications
    Assert Visible    Settings
    Tap On Text    Settings
    Assert Visible    Connected devices
    Tap On Text    Display
    Assert Visible    Brightness level
    Set Slider    Brightness level    17
    Set Slider    Brightness level    67
    Set Slider    Brightness level    89
    Tap On Description    Applications
    Assert Visible    Appium Settings
