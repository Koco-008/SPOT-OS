CC ?= cc
PKGS = gtk+-3.0 gtk-layer-shell-0 wayland-client
CFLAGS += -O2 -Wall -Wextra -Werror -Wno-unused-parameter $(shell pkg-config --cflags $(PKGS))
LDLIBS += $(shell pkg-config --libs $(PKGS))
PROTOCOL = /usr/share/wayland-protocols/stable/presentation-time/presentation-time.xml
all: spot-splash
foreign.h: foreign.xml
	wayland-scanner client-header $< $@
foreign.c: foreign.xml
	wayland-scanner private-code $< $@
presentation.h: $(PROTOCOL)
	wayland-scanner client-header $< $@
presentation.c: $(PROTOCOL)
	wayland-scanner private-code $< $@
spot-splash: splash.c presentation.h presentation.c foreign.h foreign.c
	$(CC) $(CFLAGS) splash.c presentation.c foreign.c -o $@ $(LDLIBS)
clean:
	rm -f spot-splash presentation.h presentation.c foreign.h foreign.c
.PHONY: all clean
