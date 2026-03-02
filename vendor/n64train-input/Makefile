CC      = clang
CFLAGS  = -Wall -O2 -fPIC -arch arm64 -arch x86_64
SDK     = $(shell xcrun --show-sdk-path)
CFLAGS += -I$(SDK)/usr/include
OUT     = n64train-input.dylib
SRC     = plugin.c

$(OUT): $(SRC)
	$(CC) $(CFLAGS) -dynamiclib \
	  -install_name @rpath/$(OUT) \
	  -o $(OUT) $(SRC)
	@echo "Built $(OUT)"

clean:
	rm -f $(OUT) *.o
