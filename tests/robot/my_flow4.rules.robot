*** Settings ***
Documentation     Recorded flow 'my_flow4' (rule-based conversion).
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
My Flow4
    Restart App    com.android.car.carlauncher
    Tap On Description    Applications
    Tap On Id    com.android.car.carlauncher:id/app_icon    index=14
    Assert Visible    Display
    Tap On Text    Display
    Assert Visible    Brightness level
    Set Slider    Brightness level    24
    Set Slider    Brightness level    77
    Tap On Description    Applications
    Assert Visible    Appium Settings
