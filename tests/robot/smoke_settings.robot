*** Settings ***
Documentation     Hand-written smoke test for the engine on the AAOS emulator's Settings app.
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10
Test Setup        Launch App    com.android.car.settings

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
Open Display Settings
    Tap On Text       Display
    Assert Visible    Brightness level
    Assert Visible    Adaptive brightness

Switch Between Settings Pages
    Tap On Text       Sound
    Assert Not Visible    Brightness level
    Tap On Text       Display
    Assert Visible    Brightness level
