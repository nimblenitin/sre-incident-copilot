.PHONY: all build deploy test clean

all: build deploy test

clean:
	-kind delete cluster --name alert-chatbot
