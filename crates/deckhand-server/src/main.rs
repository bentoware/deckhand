mod anki_access_contract;
mod anki_mcp_server;
mod server_shell;

use anyhow::Result;
use clap::{Parser, Subcommand};
use std::{
    fs::{File, OpenOptions},
    io::{self, Write},
    net::SocketAddr,
    path::PathBuf,
};
use tracing_subscriber::EnvFilter;

#[derive(Debug, Parser)]
#[command(name = "deckhand-server")]
#[command(about = "Deckhand companion server")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Run the local companion HTTP/SSE server.
    Serve {
        /// Address to bind for the local product server.
        #[arg(long, default_value = "127.0.0.1:28765")]
        bind: SocketAddr,

        /// Parent Anki process ID; when provided, the helper exits after the parent dies.
        #[arg(long)]
        parent_pid: Option<u32>,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    init_logging();

    let cli = Cli::parse();
    tracing::info!(command = ?cli.command, "deckhand companion starting");
    match cli.command {
        Command::Serve { bind, parent_pid } => {
            server_shell::serve(bind, parent_pid).await?;
        }
    }

    Ok(())
}

fn init_logging() {
    let filter = EnvFilter::from_default_env();
    if let Ok(path) = std::env::var("DECKHAND_COMPANION_LOG") {
        let path = PathBuf::from(path);
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Ok(file) = OpenOptions::new().create(true).append(true).open(path) {
            tracing_subscriber::fmt()
                .with_env_filter(filter)
                .with_target(false)
                .with_writer(move || {
                    TeeWriter::new(file.try_clone().expect("clone companion log file"))
                })
                .init();
            return;
        }
    }
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_target(false)
        .init();
}

struct TeeWriter {
    file: File,
}

impl TeeWriter {
    fn new(file: File) -> Self {
        Self { file }
    }
}

impl Write for TeeWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let mut stderr = io::stderr().lock();
        stderr.write_all(buf)?;
        self.file.write_all(buf)?;
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        let mut stderr = io::stderr().lock();
        stderr.flush()?;
        self.file.flush()
    }
}
