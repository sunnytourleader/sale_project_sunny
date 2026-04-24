from app import create_app


# Initializes the Flask App Factory
app = create_app()

if __name__ == '__main__':
    # host='0.0.0.0' exposes the server to your local network
    port = 5001
    app.run(host='0.0.0.0', port=port, debug=True)