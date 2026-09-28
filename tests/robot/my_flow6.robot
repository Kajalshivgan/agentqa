*** Settings ***
Documentation     Recorded flow 'my_flow6' (rule-based conversion).
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
My Flow6
    Restart App    com.android.car.carlauncher
    Tap On Description    Applications
    Tap On Text    Settings
    Assert Visible    Apps
    Scroll To Text    System    direction=up    container=com.chassis.car.ui.plugin:id/car_ui_recycler_view
    Tap On Text    System
    Tap On Text    System
