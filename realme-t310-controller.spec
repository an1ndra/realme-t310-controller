Name:           realme-t310-controller
Version:        0.0.1
Release:        rc%{?dist}
Summary:        Qt6 desktop controller for realme Buds T310
License:        MIT
URL:            https://github.com/an1ndra/realme-t310-controller
Source0:        %{name}-%{version}.tar.gz
%define debug_package %{nil}

AutoReqProv:    no
Requires:       python3
Requires:       python3-qt6
Requires:       python3-dbus

%description
A Qt6 desktop application to control realme Buds T310 earbuds. Features include:
- ANC / Normal / Transparency modes
- Game mode toggle
- EQ preset control
- Battery level monitoring (left, right, case)
- KDE system tray integration

%prep
%setup -q

%build
# No compilation needed for Python application

%install
mkdir -p %{buildroot}/usr/bin
mkdir -p %{buildroot}/usr/share/applications
mkdir -p %{buildroot}/usr/share/icons/hicolor/256x256/apps

install -m 755 qt_app/realme_t310_controller.py %{buildroot}/usr/bin/realme-t310-controller
install -m 644 %{name}.desktop %{buildroot}/usr/share/applications/%{name}.desktop
install -m 644 icons/realme-t310.png %{buildroot}/usr/share/icons/hicolor/256x256/apps/%{name}.png

%files
/usr/bin/realme-t310-controller
/usr/share/applications/realme-t310-controller.desktop
/usr/share/icons/hicolor/256x256/apps/realme-t310-controller.png

%changelog
* Mon Aug 17 2026 Realme T310 Controller <anindrakarmakar+git@proton.me> - 1.0.0-1
- Initial release