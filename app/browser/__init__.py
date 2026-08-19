"""Tarayıcı otomasyonu (Faz 11).

Motor: sistemdeki Chromium + CDP (Chrome DevTools Protocol). Playwright ya da
Selenium YOK — ikisi de ayrı bir tarayıcı indirir ve bize ek bir şey vermez;
ihtiyacımız olan her şey (canlı görüntü, tıklama, yazma, okuma) CDP'de zaten
var ve `websockets` bağımlılığı projede duruyor.

Ayrıntılı gerekçe ve güvenlik modeli: docs/OTOMASYON.md
"""

from app.browser.session import BrowserError, BrowserSession, browser

__all__ = ["BrowserError", "BrowserSession", "browser"]
