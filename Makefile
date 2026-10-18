all: mbcast mbprime
mbcast: mbcast.c
	$(CC) -O2 -Wall -Wextra -o $@ $< -lX11 -lXtst -lXi -lm
mbprime: mbprime.c
	$(CC) -O2 -Wall -Wextra -o $@ $< -lX11 -lXtst -lXi -lm
clean:
	rm -f mbcast mbprime
