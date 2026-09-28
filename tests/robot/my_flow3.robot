*** Settings ***
Documentation     Recorded flow 'my_flow3' (rule-based conversion).
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
My Flow3
    Restart App    com.android.car.settings
    Tap On Description    Applications
    Assert Visible    Appium Settings
    Tap On Text    Settings
    Assert Visible    Connected devices
    Scroll To Text    System    direction=up    container=com.chassis.car.ui.plugin:id/car_ui_recycler_view
    Tap On Text    System
    Assert Visible    Languages & input
    Tap On Text    Languages & input
    Assert Visible    Languages
    Tap On Text    Languages
    Assert Visible    Suggested
    Tap On Text    English (United States)
    Assert Visible    Autofill service
    Tap On Description    Applications
    Tap On Description    Applications
    Assert Visible    Appium Settings
