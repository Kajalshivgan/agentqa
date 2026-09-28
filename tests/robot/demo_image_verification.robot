*** Settings ***
Documentation     Minimal example: using a captured template image in a test case.
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
Home Button Is On Screen And Tappable
    Press Home
    Assert Image Visible    home_button
    Tap On Image             home_button
