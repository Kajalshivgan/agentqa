*** Settings ***
Documentation     Recorded flow 'my_flow7' (rule-based conversion).
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
My Flow7
    Restart App    com.android.car.carlauncher
    Tap On Description    Applications
    Tap On Text    Settings
    Assert Visible    Display
    Tap On Text    Display
    Set Slider    Brightness level    21
    Set Slider    Brightness level    64
    Set Slider    Brightness level    86
    Tap On Text    Adaptive brightness
    Tap On Text    Adaptive brightness
    Tap On Description    Applications
    Assert Visible    Appium Settings
