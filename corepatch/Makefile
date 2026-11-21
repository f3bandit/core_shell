# corepatch: apply Coreshell core patches on the MiSTer (static, ARM, musl: tools/musl/build-toolchain.sh in test_bench)
#   make            ARM build for the MiSTer (CC=/opt/musl-armhf/bin/arm-musl-gcc)
#   make CC=gcc     a host build (for tests)
ifeq ($(origin CC),default)
CC = /opt/musl-armhf/bin/arm-musl-gcc
endif
CFLAGS += -O2 -std=gnu11 -Wall
ifeq ($(CC),/opt/musl-armhf/bin/arm-musl-gcc)
CFLAGS += -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard
LDFLAGS += -static -s
endif
corepatch: corepatch.c
	$(CC) $(CFLAGS) -o $@ corepatch.c $(LDFLAGS)
clean:
	rm -f corepatch
