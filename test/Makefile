# Host build of the decode tests.  No SDK, no cross compiler -- decode.c is
# deliberately free of hardware dependencies so this is just cc.
#
# Built once per board header.  The two Fire 24 revisions share every signal
# mapping, so the same assertions must hold for both; building both catches
# the day someone edits one header and not the other.

CFLAGS ?= -std=c11 -O1 -g -Wall -Wextra -Wshadow -I../firmware
BUILD  := build

DEPS := test_decode.c ../firmware/decode.c ../firmware/decode.h \
        ../firmware/board.h ../firmware/board_fire24e.h \
        ../firmware/board_fire24f.h ../firmware/rom_images.h

$(BUILD)/test_decode_e: $(DEPS)
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -DMHB_BOARD_FIRE24F=0 -o $@ test_decode.c ../firmware/decode.c

$(BUILD)/test_decode_f: $(DEPS)
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -DMHB_BOARD_FIRE24F=1 -o $@ test_decode.c ../firmware/decode.c

.PHONY: check clean
check: $(BUILD)/test_decode_e $(BUILD)/test_decode_f
	./$(BUILD)/test_decode_e
	./$(BUILD)/test_decode_f
	python3 -m pytest -q test_tools.py

clean:
	rm -rf $(BUILD)
